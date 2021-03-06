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
""" Reformulation of function statements.

Consult the developer manual for information. TODO: Add ability to sync
source code comments with developer manual sections.

"""

from nuitka.nodes.AssignNodes import (
    ExpressionTargetVariableRef,
    StatementAssignmentVariable,
    StatementReleaseVariable
)
from nuitka.nodes.BuiltinRefNodes import ExpressionBuiltinRef
from nuitka.nodes.CallNodes import ExpressionCallNoKeywords
from nuitka.nodes.ConstantRefNodes import ExpressionConstantRef
from nuitka.nodes.ContainerMakingNodes import ExpressionMakeTuple
from nuitka.nodes.FunctionNodes import (
    ExpressionCoroutineBody,
    ExpressionCoroutineCreation,
    ExpressionFunctionBody,
    ExpressionFunctionCreation,
    ExpressionFunctionRef,
    ExpressionGeneratorFunctionBody
)
from nuitka.nodes.ParameterSpecs import ParameterSpec
from nuitka.nodes.ReturnNodes import StatementReturn
from nuitka.tree import SyntaxErrors
from nuitka.utils import Utils

from .Helpers import (
    buildNode,
    buildNodeList,
    buildStatementsNode,
    detectFunctionBodyKind,
    extractDocFromBody,
    getKind,
    makeDictCreationOrConstant,
    makeStatementsSequenceFromStatement,
    mangleName
)
from .ReformulationTryFinallyStatements import makeTryFinallyStatement


def _insertFinalReturnStatement(function_statements_body, source_ref):
    if function_statements_body is None:
        function_statements_body = makeStatementsSequenceFromStatement(
            statement = StatementReturn(
                expression = ExpressionConstantRef(
                    constant      = None,
                    user_provided = True,
                    source_ref    = source_ref
                ),
                source_ref = source_ref
            )
        )
    elif not function_statements_body.isStatementAborting():
        function_statements_body.setStatements(
            function_statements_body.getStatements() +
            (
                StatementReturn(
                    expression = ExpressionConstantRef(
                        constant      = None,
                        user_provided = True,
                        source_ref    = source_ref
                    ),
                    source_ref = source_ref
                ),
            )
        )

    return function_statements_body


def buildFunctionNode(provider, node, source_ref):
    # Functions have way too many details, pylint: disable=R0914

    assert getKind(node) == "FunctionDef"

    function_statements, function_doc = extractDocFromBody(node)

    function_kind = detectFunctionBodyKind(function_statements)

    if function_kind == "Function":
        function_body = ExpressionFunctionBody(
            provider   = provider,
            name       = node.name,
            doc        = function_doc,
            parameters = buildParameterSpec(provider, node.name, node, source_ref),
            is_class   = False,
            source_ref = source_ref
        )
    elif function_kind == "Generator":
        function_body = ExpressionGeneratorFunctionBody(
            provider   = provider,
            name       = node.name,
            doc        = function_doc,
            parameters = buildParameterSpec(provider, node.name, node, source_ref),
            source_ref = source_ref
        )
    else:
        assert False, function_kind

    decorators = buildNodeList(
        provider   = provider,
        nodes      = reversed(node.decorator_list),
        source_ref = source_ref
    )

    defaults = buildNodeList(
        provider   = provider,
        nodes      = node.args.defaults,
        source_ref = source_ref
    )

    kw_defaults = buildParameterKwDefaults(
        provider      = provider,
        node          = node,
        function_body = function_body,
        source_ref    = source_ref
    )

    function_statements_body = buildStatementsNode(
        provider   = function_body,
        nodes      = function_statements,
        frame      = True,
        source_ref = source_ref
    )

    if function_body.isGenerator():
        # TODO: raise generator exit?
        assert function_kind == "Generator", function_kind
    else:
        assert function_kind == "Function", function_kind

        function_statements_body = _insertFinalReturnStatement(
            function_statements_body = function_statements_body,
            source_ref               = source_ref
        )

    if function_statements_body.isStatementsFrame():
        function_statements_body = makeStatementsSequenceFromStatement(
            statement = function_statements_body
        )

    function_body.setBody(
        function_statements_body
    )

    annotations = buildParameterAnnotations(provider, node, source_ref)

    function_creation = ExpressionFunctionCreation(
        function_ref = ExpressionFunctionRef(
            function_body = function_body,
            source_ref    = source_ref
        ),
        defaults     = defaults,
        kw_defaults  = kw_defaults,
        annotations  = annotations,
        source_ref   = source_ref
    )

    # Add the "staticmethod" decorator to __new__ methods if not provided.

    # CPython made these optional, but secretly applies them when it does
    # "class __new__".  We add them earlier, so our optimization will see it.
    if node.name == "__new__" and \
       not decorators and \
       provider.isExpressionClassBody():

        decorators = (
            ExpressionBuiltinRef(
                builtin_name = "staticmethod",
                source_ref   = source_ref
            ),
        )

    decorated_function = function_creation
    for decorator in decorators:
        decorated_function = ExpressionCallNoKeywords(
            called     = decorator,
            args       = ExpressionMakeTuple(
                elements   = (decorated_function,),
                source_ref = source_ref
            ),
            source_ref = decorator.getSourceReference()
        )

    result = StatementAssignmentVariable(
        variable_ref = ExpressionTargetVariableRef(
            variable_name = mangleName(node.name, provider),
            source_ref    = source_ref
        ),
        source       = decorated_function,
        source_ref   = source_ref
    )

    if Utils.python_version >= 340:
        function_body.qualname_setup = result.getTargetVariableRef()

    return result


def buildAsyncFunctionNode(provider, node, source_ref):
    # We are creating a function here that creates coroutine objects, with
    # many details each, pylint: disable=R0914
    assert getKind(node) == "AsyncFunctionDef"

    function_statements, function_doc = extractDocFromBody(node)

    creator_function_body = ExpressionFunctionBody(
        provider   = provider,
        name       = node.name,
        doc        = function_doc,
        parameters = buildParameterSpec(provider, node.name, node, source_ref),
        is_class   = False,
        source_ref = source_ref
    )

    function_body = ExpressionCoroutineBody(
        provider   = creator_function_body,
        name       = node.name,
        source_ref = source_ref
    )

    decorators = buildNodeList(
        provider   = provider,
        nodes      = reversed(node.decorator_list),
        source_ref = source_ref
    )

    defaults = buildNodeList(
        provider   = provider,
        nodes      = node.args.defaults,
        source_ref = source_ref
    )

    function_statements_body = buildStatementsNode(
        provider   = function_body,
        nodes      = function_statements,
        frame      = True,
        source_ref = source_ref
    )

    function_statements_body = _insertFinalReturnStatement(
        function_statements_body = function_statements_body,
        source_ref               = source_ref
    )

    if function_statements_body.isStatementsFrame():
        function_statements_body = makeStatementsSequenceFromStatement(
            statement = function_statements_body
        )

    function_body.setBody(
        function_statements_body
    )

    annotations = buildParameterAnnotations(provider, node, source_ref)

    kw_defaults = buildParameterKwDefaults(
        provider      = provider,
        node          = node,
        function_body = creator_function_body,
        source_ref    = source_ref
    )

    creator_function_body.setBody(
        makeStatementsSequenceFromStatement(
            statement = StatementReturn(
                expression = ExpressionCoroutineCreation(
                    coroutine_body = function_body,
                    source_ref     = source_ref
                ),
                source_ref = source_ref
            )
        )
    )

    function_creation = ExpressionFunctionCreation(
        function_ref = ExpressionFunctionRef(
            function_body = creator_function_body,
            source_ref    = source_ref
        ),
        defaults     = defaults,
        kw_defaults  = kw_defaults,
        annotations  = annotations,
        source_ref   = source_ref
    )

    decorated_function = function_creation
    for decorator in decorators:
        decorated_function = ExpressionCallNoKeywords(
            called     = decorator,
            args       = ExpressionMakeTuple(
                elements   = (decorated_function,),
                source_ref = source_ref
            ),
            source_ref = decorator.getSourceReference()
        )


    result = StatementAssignmentVariable(
        variable_ref = ExpressionTargetVariableRef(
            variable_name = mangleName(node.name, provider),
            source_ref    = source_ref
        ),
        source       = decorated_function,
        source_ref   = source_ref
    )

    function_body.qualname_setup = result.getTargetVariableRef()

    return result


def buildParameterKwDefaults(provider, node, function_body, source_ref):
    # Build keyword only arguments default values. We are hiding here, that it
    # is a Python3 only feature.

    if Utils.python_version >= 300:
        kw_only_names = function_body.getParameters().getKwOnlyParameterNames()

        if kw_only_names:
            keys = []
            values = []

            for kw_only_name, kw_default in \
              zip(kw_only_names, node.args.kw_defaults):
                if kw_default is not None:
                    keys.append(
                        ExpressionConstantRef(
                            constant   = kw_only_name,
                            source_ref = source_ref
                        )
                    )
                    values.append(
                        buildNode(provider, kw_default, source_ref)
                    )

            kw_defaults = makeDictCreationOrConstant(
                keys       = keys,
                values     = values,
                lazy_order = False,
                source_ref = source_ref
            )
        else:
            kw_defaults = None
    else:
        kw_defaults = None

    return kw_defaults


def buildParameterAnnotations(provider, node, source_ref):
    # Too many branches, because there is too many cases, pylint: disable=R0912

    # Build annotations. We are hiding here, that it is a Python3 only feature.
    if Utils.python_version < 300:
        return None


    # Starting with Python 3.4, the names of parameters are mangled in
    # annotations as well.
    if Utils.python_version < 340:
        mangle = lambda variable_name: variable_name
    else:
        mangle = lambda variable_name: mangleName(variable_name, provider)

    keys = []
    values = []

    def addAnnotation(key, value):
        keys.append(
            ExpressionConstantRef(
                constant      = mangle(key),
                source_ref    = source_ref,
                user_provided = True
            )
        )
        values.append(value)

    def extractArg(arg):
        if getKind(arg) == "Name":
            assert arg.annotation is None
        elif getKind(arg) == "arg":
            if arg.annotation is not None:
                addAnnotation(
                    key   = arg.arg,
                    value = buildNode(provider, arg.annotation, source_ref)
                )
        elif getKind(arg) == "Tuple":
            for arg in arg.elts:
                extractArg(arg)
        else:
            assert False, getKind(arg)

    for arg in node.args.args:
        extractArg(arg)

    for arg in node.args.kwonlyargs:
        extractArg(arg)

    if Utils.python_version < 340:
        if node.args.varargannotation is not None:
            addAnnotation(
                key   = node.args.vararg,
                value = buildNode(
                    provider, node.args.varargannotation, source_ref
                )
            )

        if node.args.kwargannotation is not None:
            addAnnotation(
                key   = node.args.kwarg,
                value = buildNode(
                    provider, node.args.kwargannotation, source_ref
                )
            )
    else:
        if node.args.vararg is not None:
            extractArg(node.args.vararg)
        if node.args.kwarg is not None:
            extractArg(node.args.kwarg)

    # Return value annotation (not there for lambdas)
    if hasattr(node, "returns") and node.returns is not None:
        addAnnotation(
            key   = "return",
            value = buildNode(
                provider, node.returns, source_ref
            )
        )

    if keys:
        return makeDictCreationOrConstant(
            keys       = keys,
            values     = values,
            lazy_order = False,
            source_ref = source_ref
        )
    else:
        return None


def buildParameterSpec(provider, name, node, source_ref):
    kind = getKind(node)

    assert kind in ("FunctionDef", "Lambda", "AsyncFunctionDef"), "unsupported for kind " + kind

    def extractArg(arg):
        if arg is None:
            return None
        elif type(arg) is str:
            return mangleName(arg, provider)
        elif getKind(arg) == "Name":
            return mangleName(arg.id, provider)
        elif getKind(arg) == "arg":
            return mangleName(arg.arg, provider)
        elif getKind(arg) == "Tuple":
            return tuple(
                extractArg(arg)
                for arg in
                arg.elts
            )
        else:
            assert False, getKind(arg)

    result = ParameterSpec(
        name          = name,
        normal_args   = [
            extractArg(arg)
            for arg in
            node.args.args
        ],
        kw_only_args  = [
            extractArg(arg)
            for arg in
            node.args.kwonlyargs
            ]
              if Utils.python_version >= 300 else
            [],
        list_star_arg = extractArg(node.args.vararg),
        dict_star_arg = extractArg(node.args.kwarg),
        default_count = len(node.args.defaults)
    )

    message = result.checkValid()

    if message is not None:
        SyntaxErrors.raiseSyntaxError(
            message,
            source_ref
        )

    return result


def addFunctionVariableReleases(function):
    assert function.isExpressionFunctionBody()

    releases = []

    # We attach everything to the function definition source location.
    source_ref = function.getSourceReference()

    for variable in function.getLocalVariables():
        # Shared variables are freed by function object attachment.
        if variable.getOwner() is not function:
            continue

        # Generators have it attached at creation and release it automatically
        # when deleted.
        if function.isGenerator() and variable.isParameterVariable():
            continue

        releases.append(
            StatementReleaseVariable(
                variable   = variable,
                source_ref = source_ref
            )
        )

    if releases:
        body = function.getBody()

        if body.isStatementsFrame():
            body = makeStatementsSequenceFromStatement(
                statement = body
            )

        body = makeTryFinallyStatement(
            provider   = function,
            tried      = body,
            final      = releases,
            source_ref = source_ref
        )

        function.setBody(
            makeStatementsSequenceFromStatement(
                statement = body
            )
        )

        # assert body.isStatementAborting(), body.asXmlText()
