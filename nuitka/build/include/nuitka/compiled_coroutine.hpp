//     Copyright 2015, Kay Hayen, mailto:kay.hayen@gmail.com
//
//     Part of "Nuitka", an optimizing Python compiler that is compatible and
//     integrates with CPython, but also works on its own.
//
//     Licensed under the Apache License, Version 2.0 (the "License");
//     you may not use this file except in compliance with the License.
//     You may obtain a copy of the License at
//
//        http://www.apache.org/licenses/LICENSE-2.0
//
//     Unless required by applicable law or agreed to in writing, software
//     distributed under the License is distributed on an "AS IS" BASIS,
//     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//     See the License for the specific language governing permissions and
//     limitations under the License.
//

#ifndef __NUITKA_COMPILED_COROUTINE_H__
#define __NUITKA_COMPILED_COROUTINE_H__

#include "nuitka/compiled_generator.hpp"

#if PYTHON_VERSION >= 350

extern PyObject *Nuitka_Coroutine_New( yielder_func code, PyCellObject **closure, Py_ssize_t closure_given );
extern PyObject *Nuitka_Coroutine_New( yielder_func code );

// The Nuitka_GeneratorObject is the storage associated with a compiled
// generator object instance of which there can be many for each code.
typedef struct {
    PyObject_HEAD

    PyObject *m_name;

    PyObject *m_qualname;
    PyObject *m_yieldfrom;

    Fiber m_yielder_context;
    Fiber m_caller_context;

    // Weak references are supported for generator objects in CPython.
    PyObject *m_weakrefs;

    int m_running;

    void *m_code;

    PyObject *m_yielded;
    PyObject *m_exception_type, *m_exception_value;
    PyTracebackObject *m_exception_tb;

    PyFrameObject *m_frame;
    PyCodeObject *m_code_object;

    // Closure variables given, if any, we reference cells here.
    PyCellObject **m_closure;
    Py_ssize_t m_closure_given;

    // Parameter variable values given, if any.
    PyObject **m_parameters;
    Py_ssize_t m_parameters_given;

    // Was it ever used, is it still running, or already finished.
    Generator_Status m_status;

} Nuitka_CoroutineObject;

extern PyTypeObject Nuitka_Coroutine_Type;

typedef struct {
    PyObject_HEAD
    Nuitka_CoroutineObject *m_coroutine;
} Nuitka_CoroutineWrapperObject;

extern PyTypeObject Nuitka_CoroutineWrapper_Type;

#endif

#endif
