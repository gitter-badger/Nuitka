#     Copyright 2015, Kay Hayen, mailto:kay.hayen@gmail.com
#
#     Part of "Nuitka", an optimizing Python compiler that is compatible and
#     integrates with CPython, but also works on its own.
#
#     Licensed under the Apache License, Version 2.0 (the "License");
#     you may not use this file except in compliance with the License.
#     You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.
#
""" Nodes for functions and their creations.

Lambdas are functions too. The functions are at the core of the language and
have their complexities.

Creating a CPython function object is an optional thing. Some things might
only be used to be called directly, while knowing exactly what it is. So
the "ExpressionFunctionCreation" might be used to provide that kind of
CPython reference, and may escape.


"""

from nuitka import Options, VariableRegistry, Variables
from nuitka.optimizations.FunctionInlining import convertFunctionCallToOutline
from nuitka.tree.Extractions import updateVariableUsage
from nuitka.utils import Utils

from .Checkers import checkStatementsSequenceOrNone
from .IndicatorMixins import (
    MarkLocalsDictIndicator,
    MarkUnoptimizedFunctionIndicator
)
from .NodeBases import (
    ChildrenHavingMixin,
    ClosureGiverNodeBase,
    ClosureTakerMixin,
    CompileTimeConstantExpressionMixin,
    ExpressionChildrenHavingBase,
    ExpressionMixin,
    NodeBase,
    SideEffectsFromChildrenMixin
)
from .NodeMakingHelpers import (
    makeConstantReplacementNode,
    makeRaiseExceptionReplacementExpressionFromInstance,
    wrapExpressionWithSideEffects
)
from .ParameterSpecs import TooManyArguments, matchCall


class ExpressionFunctionBodyBase(ClosureTakerMixin, ChildrenHavingMixin,
                                 ClosureGiverNodeBase, ExpressionMixin):

    def __init__(self, provider, name, code_prefix, is_class, source_ref):
        ClosureTakerMixin.__init__(
            self,
            provider      = provider,
            early_closure = is_class
        )

        ClosureGiverNodeBase.__init__(
            self,
            name        = name,
            code_prefix = code_prefix,
            source_ref  = source_ref
        )

        ChildrenHavingMixin.__init__(
            self,
            values = {
                "body" : None # delayed
            }
        )

        # Hack: This allows some APIs to work although this is not yet
        # officially a child yet. Important during building.
        self.parent = provider

        # Python3.4: Might be overridden by global statement on the class name.
        # TODO: Make this class only code.
        if Utils.python_version >= 340:
            self.qualname_provider = provider

        self.constraint_collection = None

    @staticmethod
    def isExpressionFunctionBodyBase():
        return True

    def getFunctionQualname(self):
        """ Function __qualname__ new in CPython3.3

        Should contain some kind of full name descriptions for the closure to
        recognize and will be used for outputs.
        """

        function_name = self.getFunctionName()

        if Utils.python_version < 340:
            provider = self.getParentVariableProvider()
        else:
            provider = self.qualname_provider

        if provider.isCompiledPythonModule():
            return function_name
        elif provider.isExpressionClassBody():
            return provider.getFunctionQualname() + '.' + function_name
        else:
            return provider.getFunctionQualname() + ".<locals>." + function_name


class ExpressionFunctionBody(ExpressionFunctionBodyBase,
                             MarkLocalsDictIndicator,
                             MarkUnoptimizedFunctionIndicator):
    # We really want these many ancestors, as per design, we add properties via
    # base class mix-ins a lot, leading to many instance attributes, and
    # methods, pylint: disable=R0901,R0902

    kind = "EXPRESSION_FUNCTION_BODY"

    named_children = (
        "body",
    )

    checkers = {
        # TODO: Is "None" really an allowed value.
        "body" : checkStatementsSequenceOrNone
    }

    if Utils.python_version >= 340:
        qualname_setup = None

    def __init__(self, provider, name, doc, parameters, is_class, source_ref):
        while provider.isExpressionOutlineBody():
            provider = provider.getParentVariableProvider()

        if is_class:
            code_prefix = "class"
        else:
            # TODO: Temporarily only
            if self.isGenerator():
                code_prefix = "generator"
            else:
                code_prefix = "function"

        if name == "<lambda>":
            name = "lambda"
            code_prefix = name

            self.is_lambda = True
        else:
            self.is_lambda = False

        if name == "<listcontraction>":
            assert Utils.python_version >= 300

            code_prefix = "listcontr"
            name = ""

            self.local_locals = Utils.python_version >= 300
        else:
            self.local_locals = True

        if name == "<setcontraction>":
            code_prefix = "setcontr"
            name = ""

        if name == "<dictcontraction>":
            code_prefix = "dictcontr"
            name = ""

        if name == "<genexpr>":
            code_prefix = "genexpr"
            name = ""

            self.is_genexpr = True
        else:
            self.is_genexpr = False

        self.non_local_declarations = []

        ExpressionFunctionBodyBase.__init__(
            self,
            provider    = provider,
            name        = name,
            code_prefix = code_prefix,
            is_class    = is_class,
            source_ref  = source_ref
        )

        MarkLocalsDictIndicator.__init__(self)

        MarkUnoptimizedFunctionIndicator.__init__(self)

        self.is_class = is_class

        self.doc = doc

        # Indicator, if this is a function that uses "super", because if it
        # does, it would like to get the final "__class__" attached.
        self.has_super = False

        # Indicator if the return value exception might be required.
        self.return_exception = False

        # Indicator if the function needs to be created as a function object.
        self.needs_creation = False

        # Indicator if the function is called directly.
        self.needs_direct = False

        # Indicator if the function is used outside of where it's defined.
        self.cross_module_use = False

        self.parameters = parameters
        self.parameters.setOwner(self)

        # Register ourselves immediately with the module.
        provider.getParentModule().addFunction(self)

        self.registerProvidedVariables(
            *self.parameters.getVariables()
        )

    def getDetails(self):
        return {
            "name"       : self.getFunctionName(),
            "ref_name"   : self.getCodeName(),
            "parameters" : self.getParameters(),
            "provider"   : self.provider.getCodeName(),
            "doc"        : self.doc
        }

    def getDetail(self):
        return "named %s with %s" % (self.getFunctionName(), self.parameters)

    def getParent(self):
        assert False

    def getContainingClassDictCreation(self):
        current = self

        while not current.isCompiledPythonModule():
            if current.isExpressionClassBody():
                return current

            current = current.getParentVariableProvider()

        return None

    def getFunctionName(self):
        if self.is_lambda:
            return "<lambda>"
        elif self.is_genexpr:
            return "<genexpr>"
        else:
            return self.name

    def getDoc(self):
        return self.doc

    def getParameters(self):
        return self.parameters

    def getLocalVariableNames(self):
        return Variables.getNames(self.getLocalVariables())

    def getLocalVariables(self):
        return [
            variable for
            variable in
            self.providing.values()
            if variable.isLocalVariable()
        ]

    def getUserLocalVariables(self):
        return tuple(
            variable for
            variable in
            self.providing.values()
            if variable.isLocalVariable() and not variable.isParameterVariable()
            if variable.getOwner() is self
        )

    def getVariables(self):
        return self.providing.values()

    def removeClosureVariable(self, variable):
        assert variable in self.providing.values(), (self.providing, variable)

        del self.providing[variable.getName()]

        assert not variable.isParameterVariable() or \
               variable.getOwner() is not self

        self.taken.remove(variable)

    def demoteClosureVariable(self, variable):
        assert variable.isLocalVariable()

        self.taken.remove(variable)

        assert variable.getOwner() is not self

        new_variable = Variables.LocalVariable(
            owner         = self,
            variable_name = variable.getName()
        )

        self.providing[variable.getName()] = new_variable

        updateVariableUsage(
            provider     = self,
            old_variable = variable,
            new_variable = new_variable
        )

        VariableRegistry.addVariableUsage(new_variable, self)

    def removeUserVariable(self, variable):
        assert variable in self.providing.values(), (self.providing, variable)

        del self.providing[variable.getName()]

        assert not variable.isParameterVariable() or \
               variable.getOwner() is not self

    def getVariableForAssignment(self, variable_name):
        # print ("ASS func", self, variable_name)
        if self.hasTakenVariable(variable_name):
            result = self.getTakenVariable(variable_name)
        else:
            result = self.getProvidedVariable(variable_name)

        return result

    def getVariableForReference(self, variable_name):
        # print ( "REF func", self, variable_name )

        if self.hasProvidedVariable(variable_name):
            result = self.getProvidedVariable(variable_name)
        else:
            result = self.getClosureVariable(
                variable_name = variable_name
            )

            # Remember that we need that closure variable for something, so
            # we don't create it again all the time.
            if not result.isModuleVariable():
                self.registerProvidedVariable(result)

            # For "exec" containing/star import containing, we get a
            # closure variable already, but if it is a module variable,
            # only then make it a maybe local variable.
            if self.isUnoptimized() and result.isModuleVariable():
                result = Variables.MaybeLocalVariable(
                    owner          = self,
                    maybe_variable = result
                )

                self.registerProvidedVariable(result)

        return result

    def getVariableForClosure(self, variable_name):
        # print( "getVariableForClosure", self, variable_name )

        if self.hasProvidedVariable(variable_name):
            return self.getProvidedVariable(variable_name)
        else:
            return self.provider.getVariableForClosure(variable_name)

    def createProvidedVariable(self, variable_name):
        # print("createProvidedVariable", self, variable_name)

        if self.local_locals:
            return Variables.LocalVariable(
                owner         = self,
                variable_name = variable_name
            )
        else:
            # Make sure the provider knows it has to provide a variable of this
            # name for the assignment.
            self.provider.getVariableForAssignment(
                variable_name = variable_name
            )

            return self.getClosureVariable(
                variable_name = variable_name
            )

    def addNonlocalsDeclaration(self, names, source_ref):
        self.non_local_declarations.append(
            (names, source_ref)
        )

    def getNonlocalDeclarations(self):
        return self.non_local_declarations

    getBody = ChildrenHavingMixin.childGetter("body")
    setBody = ChildrenHavingMixin.childSetter("body")

    def needsCreation(self):
        return self.needs_creation

    def markAsNeedsCreation(self):
        self.needs_creation = True

    def needsDirectCall(self):
        return self.needs_direct

    def markAsDirectlyCalled(self):
        self.needs_direct = True

    def isCrossModuleUsed(self):
        return self.cross_module_use

    def markAsCrossModuleUsed(self):
        self.cross_module_use = True

    def computeExpression(self, constraint_collection):
        assert False

        # Function body is quite irreplaceable.
        return self, None, None

    def getLocalsMode(self):
        if Utils.python_version >= 300:
            return "updated"
        elif self.isEarlyClosure() or self.isUnoptimized():
            return "updated"
        else:
            return "copy"

    def computeExpressionCall(self, call_node, call_args, call_kw,
                              constraint_collection):
        # TODO: Until we have something to re-order the arguments, we need to
        # skip this. For the immediate need, we avoid this complexity, as a
        # re-ordering will be needed.

        assert False, self

    def isCompileTimeConstant(self):
        # TODO: It's actually pretty much compile time accessible maybe.
        return None

    def mayHaveSideEffects(self):
        # The function definition has no side effects, calculating the defaults
        # would be, but that is done outside of this.
        return False

    def mayRaiseException(self, exception_type):
        return self.getBody().mayRaiseException(exception_type)

    def markAsClassClosureTaker(self):
        self.has_super = True

    def isClassClosureTaker(self):
        return self.has_super

    def markAsExceptionReturnValue(self):
        self.return_exception = True

    def needsExceptionReturnValue(self):
        return self.return_exception

    @staticmethod
    def isGenerator():
        return False


class ExpressionGeneratorFunctionBody(ExpressionFunctionBody):
    # We really want these many ancestors, as per design, we add properties via
    # base class mix-ins a lot, leading to many instance attributes, and
    # methods, pylint: disable=R0901

    kind = "EXPRESSION_GENERATOR_FUNCTION_BODY"

    named_children = (
        "body",
    )

    checkers = {
        # TODO: Is "None" really an allowed value.
        "body" : checkStatementsSequenceOrNone
    }

    if Utils.python_version >= 340:
        qualname_setup = None

    def __init__(self, provider, name, doc, parameters, source_ref):
        ExpressionFunctionBody.__init__(
            self,
            provider   = provider,
            name       = name,
            doc        = doc,
            parameters = parameters,
            is_class   = False,
            source_ref = source_ref
        )

        self.needs_generator_return_exit = False

    @staticmethod
    def isExpressionFunctionBody():
        return True

    @staticmethod
    def isGenerator():
        return True

    def markAsNeedsGeneratorReturnHandling(self, value):
        self.needs_generator_return_exit = max(
            self.needs_generator_return_exit,
            value
        )

    def needsGeneratorReturnHandling(self):
        return self.needs_generator_return_exit == 2

    def needsGeneratorReturnExit(self):
        return bool(self.needs_generator_return_exit)


class ExpressionClassBody(ExpressionFunctionBody):
    # We really want these many ancestors, as per design, we add properties via
    # base class mix-ins a lot, leading to many instance attributes, and
    # methods, pylint: disable=R0901

    kind = "EXPRESSION_CLASS_BODY"

    named_children = (
        "body",
    )

    checkers = {
        # TODO: Is "None" really an allowed value.
        "body" : checkStatementsSequenceOrNone
    }

    # TODO: Everybody should check base instead.
    @staticmethod
    def isExpressionFunctionBody():
        return True

    def getVariableForClosure(self, variable_name):
        # print( "getVariableForClosure", self, variable_name )

        # The class bodies provide no closure, except under CPython3.x, there
        # they provide "__class__" but nothing else.

        if variable_name == "__class__":
            if Utils.python_version < 300:
                return self.provider.getVariableForClosure(
                    variable_name
                )
            elif Utils.python_version >= 340 and False: # TODO: Temporarily reverted
                result = self.getTempVariable(
                    temp_scope = None,
                    name       = "__class__"
                )

                return result
            else:
                return ExpressionFunctionBody.getVariableForClosure(
                    self,
                    variable_name = variable_name
                )
        else:
            return self.provider.getVariableForClosure(
                variable_name
            )


def convertNoneConstantOrEmptyDictToNone(node):
    if node is None:
        return None
    elif node.isExpressionConstantRef() and node.getConstant() is None:
        return None
    elif node.isExpressionConstantRef() and node.getConstant() == {}:
        return None
    else:
        return node

# TODO: Function direct call node ought to be here too.

class ExpressionFunctionCreation(SideEffectsFromChildrenMixin,
                                 ExpressionChildrenHavingBase):

    kind = "EXPRESSION_FUNCTION_CREATION"

    # Note: The order of evaluation for these is a bit unexpected, but
    # true. Keyword defaults go first, then normal defaults, and annotations of
    # all kinds go last.

    # A bug of CPython3.x not fixed before version 3.4, see bugs.python.org/issue16967
    kw_defaults_before_defaults = Utils.python_version < 340

    if kw_defaults_before_defaults:
        named_children = (
            "kw_defaults", "defaults", "annotations", "function_ref"
        )
    else:
        named_children = (
            "defaults", "kw_defaults", "annotations", "function_ref"
        )

    checkers   = {
        "kw_defaults" : convertNoneConstantOrEmptyDictToNone,
    }

    def __init__(self, function_ref, defaults, kw_defaults, annotations,
                 source_ref):
        assert kw_defaults is None or kw_defaults.isExpression()
        assert annotations is None or annotations.isExpression()
        assert function_ref.isExpressionFunctionRef()

        ExpressionChildrenHavingBase.__init__(
            self,
            values     = {
                "function_ref" : function_ref,
                "defaults"     : tuple(defaults),
                "kw_defaults"  : kw_defaults,
                "annotations"  : annotations
            },
            source_ref = source_ref
        )

    def getName(self):
        return self.getFunctionRef().getName()


    def computeExpression(self, constraint_collection):
        defaults = self.getDefaults()

        side_effects = []

        for default in defaults:
            if default.willRaiseException(BaseException):
                result = wrapExpressionWithSideEffects(
                    side_effects = side_effects,
                    old_node     = self,
                    new_node     = default
                )

                return result, "new_raise", "Default value contains raise."

        kw_defaults = self.getKwDefaults()

        if kw_defaults is not None:
            if kw_defaults.willRaiseException(BaseException):
                result = wrapExpressionWithSideEffects(
                    side_effects = side_effects,
                    old_node     = self,
                    new_node     = kw_defaults
                )

                return result, "new_raise", "Keyword default values contain raise."

            side_effects.append(kw_defaults)

        annotations = self.getAnnotations()

        if annotations is not None and annotations.willRaiseException(BaseException):
            result = wrapExpressionWithSideEffects(
                side_effects = side_effects,
                old_node     = self,
                new_node     = annotations
            )

            return result, "new_raise", "Annotation values contain raise."

        # TODO: Function body may know something too.
        return self, None, None

    getFunctionRef = ExpressionChildrenHavingBase.childGetter("function_ref")
    getDefaults = ExpressionChildrenHavingBase.childGetter("defaults")
    getKwDefaults = ExpressionChildrenHavingBase.childGetter("kw_defaults")
    getAnnotations = ExpressionChildrenHavingBase.childGetter("annotations")

    def mayRaiseException(self, exception_type):
        for default in self.getDefaults():
            if default.mayRaiseException(exception_type):
                return True

        kw_defaults = self.getKwDefaults()

        if kw_defaults is not None and kw_defaults.mayRaiseException(exception_type):
            return True

        annotations = self.getAnnotations()

        if annotations is not None and annotations.mayRaiseException(exception_type):
            return True

        return False

    def computeExpressionCall(self, call_node, call_args, call_kw,
                              constraint_collection):

        constraint_collection.onExceptionRaiseExit(BaseException)

        # TODO: Until we have something to re-order the keyword arguments, we
        # need to skip this. For the immediate need, we avoid this complexity,
        # as a re-ordering will be needed.
        if call_kw is not None and \
           (not call_kw.isExpressionConstantRef() or call_kw.getConstant() != {}):
            return call_node, None, None

        if call_kw is not None:
            return call_node, None, None

        if call_args is None:
            args_tuple = ()
        elif call_args.isExpressionConstantRef() or \
             call_args.isExpressionMakeTuple():
            args_tuple = call_args.getIterationValues()
        else:
            # TODO: Can this even happen, i.e. does the above check make
            # sense.
            assert False, call_args

            return call_node, None, None

        function_body = self.getFunctionRef().getFunctionBody()

        # TODO: Actually the above disables it entirely, as it is at least
        # the empty dictionary node in any case. We will need some enhanced
        # interfaces for "matchCall" to work on.

        call_spec = function_body.getParameters()

        try:
            args_dict = matchCall(
                func_name     = self.getName(),
                args          = call_spec.getArgumentNames(),
                star_list_arg = call_spec.getStarListArgumentName(),
                star_dict_arg = call_spec.getStarDictArgumentName(),
                num_defaults  = call_spec.getDefaultCount(),
                positional    = args_tuple,
                pairs         = ()
            )

            values = [
                args_dict[name]
                for name in
                call_spec.getAllNames()
            ]

            result = ExpressionFunctionCall(
                function   = self,
                values     = values,
                source_ref = call_node.getSourceReference()
            )

            return (
                result,
                "new_statements", # TODO: More appropriate tag maybe.
                """\
Replaced call to created function body '%s' with direct \
function call.""" % self.getName()
            )

        except TooManyArguments as e:
            result = wrapExpressionWithSideEffects(
                new_node     = makeRaiseExceptionReplacementExpressionFromInstance(
                    expression = call_node,
                    exception  = e.getRealException()
                ),
                old_node     = call_node,
                side_effects = call_node.extractPreCallSideEffects()
            )

            return (
                result,
                "new_raise", # TODO: More appropriate tag maybe.
                """Replaced call to created function body '%s' to argument \
error""" % self.getName()
            )

    def getCallCost(self, values):
        # TODO: Ought to use values. If they are all constant, how about we
        # assume no cost, pylint: disable=W0613

        if not Options.isExperimental():
            return None

        function_body = self.getFunctionRef().getFunctionBody()

        if function_body.isGenerator():
            return None

        if function_body.isExpressionClassBody():
            return None

        # TODO: Lying for the demo, this is too limiting, but needs frames to
        # be allowed twice in a context.
        if function_body.mayRaiseException(BaseException):
            return 60

        return 20

    def createOutlineFromCall(self, provider, values):
        return convertFunctionCallToOutline(
            provider     = provider,
            function_ref = self.getFunctionRef(),
            values       = values
        )



class ExpressionFunctionRef(NodeBase, ExpressionMixin):
    kind = "EXPRESSION_FUNCTION_REF"

    def __init__(self, function_body, source_ref):
        assert function_body.isExpressionFunctionBody()

        NodeBase.__init__(
            self,
            source_ref = source_ref
        )

        self.function_body = function_body

    def getName(self):
        return self.function_body.getName()

    def getDetails(self):
        return {
            "function_body" : self.function_body
        }

    def getDetailsForDisplay(self):
        return {
            "function" : self.function_body.getCodeName()
        }

    def getFunctionBody(self):
        return self.function_body

    def computeExpressionRaw(self, constraint_collection):
        function_body = self.getFunctionBody()

        owning_module = function_body.getParentModule()

        # Make sure the owning module is added to the used set. This is most
        # important for helper functions, or modules, which otherwise have
        # become unused.
        from nuitka.ModuleRegistry import addUsedModule
        addUsedModule(owning_module)

        owning_module.addUsedFunction(function_body)

        from nuitka.optimizations.TraceCollections import \
            ConstraintCollectionFunction

        # TODO: Doesn't this mean, we can do this multiple times by doing it
        # in the reference. We should do it in the body, and there we should
        # limit us to only doing it once per module run, e.g. being based on
        # presence in used functions of the module already.
        old_collection = function_body.constraint_collection

        function_body.constraint_collection = ConstraintCollectionFunction(
            parent        = constraint_collection,
            function_body = function_body
        )

        statements_sequence = function_body.getBody()

        if statements_sequence is not None and \
           not statements_sequence.getStatements():
            function_body.setStatements(None)
            statements_sequence = None

        if statements_sequence is not None:
            result = statements_sequence.computeStatementsSequence(
                constraint_collection = function_body.constraint_collection
            )

            if result is not statements_sequence:
                function_body.setBody(result)

        function_body.constraint_collection.updateFromCollection(old_collection)

        # TODO: Function collection may now know something.
        return self, None, None

    def mayHaveSideEffects(self):
        # Using a function has no side effects.
        return False


class ExpressionFunctionCall(ExpressionChildrenHavingBase):
    """ Shared function call.

        This is for calling created function bodies with multiple users. Not
        clear if such a thing should exist. But what this will do is to have
        respect for the fact that there are multiple such calls.
    """

    kind = "EXPRESSION_FUNCTION_CALL"

    named_children = (
        "function",
        "values"
    )

    def __init__(self, function, values, source_ref):
        assert function.isExpressionFunctionCreation()

        ExpressionChildrenHavingBase.__init__(
            self,
            values     = {
                "function" : function,
                "values"   : tuple(values),
            },
            source_ref = source_ref
        )

    def computeExpression(self, constraint_collection):
        function = self.getFunction()

        values = self.getArgumentValues()

        # TODO: This needs some design.
        cost = function.getCallCost(values)

        if function.getFunctionRef().getFunctionBody().mayRaiseException(BaseException):
            constraint_collection.onExceptionRaiseExit(BaseException)

        if cost is not None and cost < 50:
            result = function.createOutlineFromCall(
                provider = self.getParentVariableProvider(),
                values   = values
            )

            return result, "new_statements", "Function call in-lined."

        return self, None, None

    getFunction = ExpressionChildrenHavingBase.childGetter("function")
    getArgumentValues = ExpressionChildrenHavingBase.childGetter("values")


# Needed for Python3.3 and higher
class ExpressionFunctionQualnameRef(CompileTimeConstantExpressionMixin,
                                    NodeBase):
    kind = "EXPRESSION_FUNCTION_QUALNAME_REF"
    def __init__(self, function_body, source_ref):
        NodeBase.__init__(self, source_ref = source_ref)
        CompileTimeConstantExpressionMixin.__init__(self)

        self.function_body = function_body

    def computeExpression(self, constraint_collection):
        result = makeConstantReplacementNode(
            node     = self,
            constant = self.function_body.getFunctionQualname()
        )

        return result, "new_constant", "Delayed __qualname__ resolution."


class ExpressionCoroutineCreation(NodeBase, ExpressionMixin):
    kind = "EXPRESSION_COROUTINE_CREATION"

    def __init__(self, coroutine_body, source_ref):
        assert coroutine_body.isExpressionCoroutineBody()

        NodeBase.__init__(
            self,
            source_ref = source_ref
        )

        self.coroutine_body = coroutine_body

    def getName(self):
        return self.coroutine_body.getName()

    def getDetails(self):
        return {
            "coroutine_body" : self.coroutine_body
        }

    def getDetailsForDisplay(self):
        return {
            "coroutine" : self.coroutine_body.getCodeName()
        }

    def getCoroutineBody(self):
        return self.coroutine_body

    def computeExpressionRaw(self, constraint_collection):
        function_body = self.getCoroutineBody()

        owning_module = function_body.getParentModule()

        # Make sure the owning module is added to the used set. This is most
        # important for helper functions, or modules, which otherwise have
        # become unused.
        from nuitka.ModuleRegistry import addUsedModule
        addUsedModule(owning_module)

        owning_module.addUsedFunction(function_body)

        from nuitka.optimizations.TraceCollections import \
            ConstraintCollectionFunction

        # TODO: Doesn't this mean, we can do this multiple times by doing it
        # in the reference. We should do it in the body, and there we should
        # limit us to only doing it once per module run, e.g. being based on
        # presence in used functions of the module already.
        old_collection = function_body.constraint_collection

        function_body.constraint_collection = ConstraintCollectionFunction(
            parent        = constraint_collection,
            function_body = function_body
        )

        statements_sequence = function_body.getBody()

        if statements_sequence is not None and \
           not statements_sequence.getStatements():
            function_body.setStatements(None)
            statements_sequence = None

        if statements_sequence is not None:
            result = statements_sequence.computeStatementsSequence(
                constraint_collection = function_body.constraint_collection
            )

            if result is not statements_sequence:
                function_body.setBody(result)

        function_body.constraint_collection.updateFromCollection(old_collection)

        # TODO: Function collection may now know something.
        return self, None, None

    def mayRaiseException(self, exception_type):
        return False

    def mayHaveSideEffects(self):
        return False




class ExpressionCoroutineBody(ExpressionFunctionBodyBase):
    kind = "EXPRESSION_COROUTINE_BODY"

    named_children = (
        "body",
    )

    checkers = {
        # TODO: Is "None" really an allowed value.
        "body" : checkStatementsSequenceOrNone
    }

    if Utils.python_version >= 340:
        qualname_setup = None

    def __init__(self, provider, name, source_ref):
        while provider.isExpressionOutlineBody():
            provider = provider.getParentVariableProvider()

        ExpressionFunctionBodyBase.__init__(
            self,
            provider    = provider,
            name        = name,
            code_prefix = "coroutine",
            is_class    = False,
            source_ref  = source_ref
        )

    getBody = ChildrenHavingMixin.childGetter("body")
    setBody = ChildrenHavingMixin.childSetter("body")

    def getFunctionName(self):
        return self.name
