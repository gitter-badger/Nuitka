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
#include "nuitka/prelude.hpp"

static PyObject *Nuitka_Coroutine_get_name( Nuitka_CoroutineObject *coroutine)
{
    return INCREASE_REFCOUNT( coroutine->m_name );
}

static int Nuitka_Coroutine_set_name( Nuitka_CoroutineObject *coroutine, PyObject *value )
{
    // Cannot be deleted, not be non-unicode value.
    if (unlikely( ( value == NULL ) || !PyUnicode_Check( value ) ))
    {
        PyErr_Format(
            PyExc_TypeError,
            "__name__ must be set to a string object"
        );

        return -1;
    }

    PyObject *tmp = coroutine->m_name;
    Py_INCREF( value );
    coroutine->m_name = value;
    Py_DECREF( tmp );

    return 0;
}

static PyObject *Nuitka_Coroutine_get_qualname( Nuitka_CoroutineObject *coroutine )
{
    return INCREASE_REFCOUNT( coroutine->m_qualname );
}

static int Nuitka_Coroutine_set_qualname( Nuitka_CoroutineObject *coroutine, PyObject *value )
{
    // Cannot be deleted, not be non-unicode value.
    if (unlikely( ( value == NULL ) || !PyUnicode_Check( value ) ))
    {
        PyErr_Format(
            PyExc_TypeError,
            "__qualname__ must be set to a string object"
        );

        return -1;
    }

    PyObject *tmp = coroutine->m_qualname;
    Py_INCREF( value );
    coroutine->m_qualname = value;
    Py_DECREF( tmp );

    return 0;
}

static PyObject *Nuitka_Coroutine_get_cr_await( Nuitka_CoroutineObject *coroutine )
{
    if ( coroutine->m_yieldfrom )
    {
        Py_INCREF( coroutine->m_yieldfrom );
        return coroutine->m_yieldfrom;
    }
    else
    {
        Py_INCREF( Py_None );
        return Py_None;
    }
}

static PyObject *Nuitka_Coroutine_get_code( Nuitka_CoroutineObject *coroutine )
{
    return INCREASE_REFCOUNT( (PyObject *)coroutine->m_code_object );
}

static int Nuitka_Coroutine_set_code( Nuitka_CoroutineObject *coroutine, PyObject *value )
{
    PyErr_Format( PyExc_RuntimeError, "cr_code is not writable in Nuitka" );
    return -1;
}

static PyObject *Nuitka_Coroutine_get_frame( Nuitka_CoroutineObject *coroutine )
{
    if ( coroutine->m_frame )
    {
        return INCREASE_REFCOUNT( (PyObject *)coroutine->m_frame );
    }
    else
    {
        return INCREASE_REFCOUNT( Py_None );
    }
}

static int Nuitka_Coroutine_set_frame( Nuitka_CoroutineObject *coroutine, PyObject *value )
{
    PyErr_Format( PyExc_RuntimeError, "gi_frame is not writable in Nuitka" );
    return -1;
}


static void Nuitka_Coroutine_release_parameters( Nuitka_CoroutineObject *coroutine )
{
    if ( coroutine->m_parameters )
    {
        for( Py_ssize_t i = 0; i < coroutine->m_parameters_given; i++ )
        {
            Py_XDECREF( coroutine->m_parameters[i] );
            coroutine->m_parameters[i] = NULL;
        }
    }

    coroutine->m_parameters = NULL;
}

static PyObject *Nuitka_Coroutine_send( Nuitka_CoroutineObject *coroutine, PyObject *value )
{
    if ( coroutine->m_status == status_Unused && value != NULL && value != Py_None )
    {
        PyErr_Format( PyExc_TypeError, "can't send non-None value to a just-started generator" );
        return NULL;
    }

    if ( coroutine->m_status != status_Finished )
    {
        PyThreadState *thread_state = PyThreadState_GET();

#if PYTHON_VERSION < 300
        PyObject *saved_exception_type = thread_state->exc_type;
        Py_XINCREF( saved_exception_type );
        PyObject *saved_exception_value = thread_state->exc_value;
        Py_XINCREF( saved_exception_value );
        PyTracebackObject *saved_exception_traceback = (PyTracebackObject *)thread_state->exc_traceback;
        Py_XINCREF( saved_exception_traceback );
#endif

        if ( coroutine->m_running )
        {
            PyErr_Format( PyExc_ValueError, "generator already executing" );
            return NULL;
        }

        if ( coroutine->m_status == status_Unused )
        {
            // Prepare the generator context to run.
            int res = prepareFiber( &coroutine->m_yielder_context, coroutine->m_code, (uintptr_t)coroutine );

            if ( res != 0 )
            {
                PyErr_Format( PyExc_MemoryError, "coroutine cannot be allocated" );
                return NULL;
            }

            coroutine->m_status = status_Running;
        }

        coroutine->m_yielded = value;

        // Put the generator back on the frame stack.
        PyFrameObject *return_frame = thread_state->frame;
#ifndef __NUITKA_NO_ASSERT__
        if ( return_frame )
        {
            assertFrameObject( return_frame );
        }
#endif

        if ( coroutine->m_frame )
        {
            // It would be nice if our frame were still alive. Nobody had the
            // right to release it.
            assertFrameObject( coroutine->m_frame );

            // It's not supposed to be on the top right now.
            assert( return_frame != coroutine->m_frame );

            Py_XINCREF( return_frame );
            coroutine->m_frame->f_back = return_frame;

            thread_state->frame = coroutine->m_frame;
        }

        // Continue the yielder function while preventing recursion.
        coroutine->m_running = true;

        swapFiber( &coroutine->m_caller_context, &coroutine->m_yielder_context );

        coroutine->m_running = false;

        thread_state = PyThreadState_GET();

        // Remove the generator from the frame stack.
        if ( coroutine->m_frame )
        {
            assert( thread_state->frame == coroutine->m_frame );
            assertFrameObject( coroutine->m_frame );

            Py_CLEAR( coroutine->m_frame->f_back );
        }

        thread_state->frame = return_frame;

        if ( coroutine->m_yielded == NULL )
        {
            assert( ERROR_OCCURRED() );

            coroutine->m_status = status_Finished;

            Py_XDECREF( coroutine->m_frame );
            coroutine->m_frame = NULL;

            Nuitka_Coroutine_release_parameters( coroutine );

            assert( ERROR_OCCURRED() );

            if ( coroutine->m_code_object->co_flags & CO_FUTURE_GENERATOR_STOP &&
                 GET_ERROR_OCCURRED() == PyExc_StopIteration )
            {
                PyObject *saved_exception_type, *saved_exception_value;
                PyTracebackObject *saved_exception_tb;

                // TODO: Needs release, should get reference count test.
                FETCH_ERROR_OCCURRED( &saved_exception_type, &saved_exception_value, &saved_exception_tb );

                PyObject *exception_type = CALL_FUNCTION_WITH_ARGS1(
                    PyExc_RuntimeError,
                    PyUnicode_FromString("generator raised StopIteration")
                );
                PyObject *exception_value = NULL;
                PyTracebackObject *exception_tb = NULL;

                RAISE_EXCEPTION_WITH_CAUSE(
                    &exception_type,
                    &exception_value,
                    &exception_tb,
                    saved_exception_value
                );
                PyException_SetContext( exception_value, saved_exception_value );

                RESTORE_ERROR_OCCURRED( exception_type, exception_value, exception_tb );
            }

            return NULL;
        }
        else
        {
            return coroutine->m_yielded;
        }
    }
    else
    {
        PyErr_SetObject( PyExc_StopIteration, (PyObject *)NULL );

        return NULL;
    }
}

PyObject *Nuitka_Coroutine_close( Nuitka_CoroutineObject *coroutine, PyObject *args )
{
    if ( coroutine->m_status == status_Running )
    {
        coroutine->m_exception_type = INCREASE_REFCOUNT( PyExc_GeneratorExit );
        coroutine->m_exception_value = NULL;
        coroutine->m_exception_tb = NULL;

        PyObject *result = Nuitka_Coroutine_send( coroutine, Py_None );

        if (unlikely( result ))
        {
            Py_DECREF( result );

            PyErr_Format( PyExc_RuntimeError, "coroutine ignored GeneratorExit" );
            return NULL;
        }
        else
        {
            PyObject *error = GET_ERROR_OCCURRED();
            assert( error != NULL );

            if ( EXCEPTION_MATCH_GENERATOR( error ) )
            {
                CLEAR_ERROR_OCCURRED();

                return INCREASE_REFCOUNT( Py_None );
            }

            return NULL;
        }
    }

    return INCREASE_REFCOUNT( Py_None );
}

static PyObject *Nuitka_Coroutine_throw( Nuitka_CoroutineObject *coroutine, PyObject *args )
{
    assert( coroutine->m_exception_type == NULL );
    assert( coroutine->m_exception_value == NULL );
    assert( coroutine->m_exception_tb == NULL );

    int res = PyArg_UnpackTuple( args, "throw", 1, 3, &coroutine->m_exception_type, &coroutine->m_exception_value, (PyObject **)&coroutine->m_exception_tb );

    if (unlikely( res == 0 ))
    {
        coroutine->m_exception_type = NULL;
        coroutine->m_exception_value = NULL;
        coroutine->m_exception_tb = NULL;

        return NULL;
    }

    if ( (PyObject *)coroutine->m_exception_tb == Py_None )
    {
        coroutine->m_exception_tb = NULL;
    }
    else if ( coroutine->m_exception_tb != NULL && !PyTraceBack_Check( coroutine->m_exception_tb ) )
    {
        coroutine->m_exception_type = NULL;
        coroutine->m_exception_value = NULL;
        coroutine->m_exception_tb = NULL;

        PyErr_Format( PyExc_TypeError, "throw() third argument must be a traceback object" );
        return NULL;
    }

    if ( PyExceptionClass_Check( coroutine->m_exception_type ))
    {
        Py_INCREF( coroutine->m_exception_type );
        Py_XINCREF( coroutine->m_exception_value );
        Py_XINCREF( coroutine->m_exception_tb );

        NORMALIZE_EXCEPTION( &coroutine->m_exception_type, &coroutine->m_exception_value, &coroutine->m_exception_tb );
    }
    else if ( PyExceptionInstance_Check( coroutine->m_exception_type ) )
    {
        if ( coroutine->m_exception_value && coroutine->m_exception_value != Py_None )
        {
            coroutine->m_exception_type = NULL;
            coroutine->m_exception_value = NULL;
            coroutine->m_exception_tb = NULL;

            PyErr_Format( PyExc_TypeError, "instance exception may not have a separate value" );
            return NULL;
        }
        coroutine->m_exception_value = coroutine->m_exception_type;
        Py_INCREF( coroutine->m_exception_value );
        coroutine->m_exception_type = PyExceptionInstance_Class( coroutine->m_exception_type );
        Py_INCREF( coroutine->m_exception_type );
        Py_XINCREF( coroutine->m_exception_tb );
    }
    else
    {
        PyErr_Format(
            PyExc_TypeError,
            "exceptions must be classes or instances deriving from BaseException, not %s",
            Py_TYPE( coroutine->m_exception_type )->tp_name
        );

        coroutine->m_exception_type = NULL;
        coroutine->m_exception_value = NULL;
        coroutine->m_exception_tb = NULL;

        return NULL;
    }

    if ( ( coroutine->m_exception_tb != NULL ) && ( (PyObject *)coroutine->m_exception_tb != Py_None ) && ( !PyTraceBack_Check( coroutine->m_exception_tb ) ) )
    {
        PyErr_Format( PyExc_TypeError, "throw() third argument must be a traceback object" );
        return NULL;
    }

    if ( coroutine->m_status != status_Finished )
    {
        PyObject *result = Nuitka_Coroutine_send( coroutine, Py_None );

        return result;
    }
    else
    {
        RESTORE_ERROR_OCCURRED( coroutine->m_exception_type, coroutine->m_exception_value, coroutine->m_exception_tb );

        coroutine->m_exception_type = NULL;
        coroutine->m_exception_value = NULL;
        coroutine->m_exception_tb = NULL;

        return NULL;
    }
}

static void Nuitka_Coroutine_tp_del( Nuitka_CoroutineObject *coroutine )
{
    if ( coroutine->m_status != status_Running )
    {
        return;
    }

    // Revive temporarily.
    assert( Py_REFCNT( coroutine ) == 0 );
    Py_REFCNT( coroutine ) = 1;

    PyObject *error_type, *error_value;
    PyTracebackObject *error_traceback;

    FETCH_ERROR_OCCURRED( &error_type, &error_value, &error_traceback );

    PyObject *result = Nuitka_Coroutine_close( coroutine, NULL );

    if (unlikely( result == NULL ))
    {
        PyErr_WriteUnraisable( (PyObject *)coroutine );
    }
    else
    {
        Py_DECREF( result );
    }

    /* Restore the saved exception. */
    RESTORE_ERROR_OCCURRED( error_type, error_value, error_traceback );

    assert( Py_REFCNT( coroutine ) > 0 );
    Py_REFCNT( coroutine ) -= 1;

    Py_ssize_t refcnt = Py_REFCNT( coroutine );

    if (unlikely( refcnt != 0 ))
    {
        _Py_NewReference( (PyObject *)coroutine );
        Py_REFCNT( coroutine ) = refcnt;

        _Py_DEC_REFTOTAL;
    }
}

static void Nuitka_Coroutine_tp_dealloc( Nuitka_CoroutineObject *coroutine )
{
    assert( Py_REFCNT( coroutine ) == 0 );
    Py_REFCNT( coroutine ) = 1;

    // Save the current exception, if any, we must preserve it.
    PyObject *save_exception_type, *save_exception_value;
    PyTracebackObject *save_exception_tb;
    FETCH_ERROR_OCCURRED( &save_exception_type, &save_exception_value, &save_exception_tb );

    PyObject *close_result = Nuitka_Coroutine_close( coroutine, NULL );

    if (unlikely( close_result == NULL ))
    {
        PyErr_WriteUnraisable( (PyObject *)coroutine );
    }
    else
    {
        Py_DECREF( close_result );
    }

    Nuitka_Coroutine_release_parameters( coroutine );

    if ( coroutine->m_parameters_given ) free( coroutine->m_parameters );
    if ( coroutine->m_closure_given )
    {
        for( Py_ssize_t i = 0; i < coroutine->m_closure_given; i++ )
        {
            Py_DECREF( coroutine->m_closure[ i ] );
        }
        free( coroutine->m_closure );
    }

    Py_XDECREF( coroutine->m_frame );

    assert( Py_REFCNT( coroutine ) == 1 );
    Py_REFCNT( coroutine ) = 0;

    releaseFiber( &coroutine->m_yielder_context );

    // Now it is safe to release references and memory for it.
    Nuitka_GC_UnTrack( coroutine );

    if ( coroutine->m_weakrefs != NULL )
    {
        PyObject_ClearWeakRefs( (PyObject *)coroutine );
        assert( !ERROR_OCCURRED() );
    }

    Py_DECREF( coroutine->m_name );
    Py_DECREF( coroutine->m_qualname );

    PyObject_GC_Del( coroutine );
    RESTORE_ERROR_OCCURRED( save_exception_type, save_exception_value, save_exception_tb );
}


static PyObject *Nuitka_Coroutine_tp_repr( Nuitka_CoroutineObject *coroutine )
{
    return PyUnicode_FromFormat(
        "<compiled_coroutine object %s at %p>",
        Nuitka_String_AsString( coroutine->m_qualname ),
        coroutine
    );
}


static long Nuitka_Coroutine_tp_traverse( PyObject *coroutine, visitproc visit, void *arg )
{
    // TODO: Identify the impact of not visiting owned objects and/or if it
    // could be NULL instead. The "methodobject" visits its self and module. I
    // understand this is probably so that back references of this function to
    // its upper do not make it stay in the memory. A specific test if that
    // works might be needed.
    return 0;
}

static PyObject *Nuitka_Coroutine_await( Nuitka_CoroutineObject *coroutine )
{
    Nuitka_CoroutineWrapperObject *result = PyObject_GC_New(Nuitka_CoroutineWrapperObject, &Nuitka_CoroutineWrapper_Type);

    if (unlikely(result == NULL))
    {
        return NULL;
    }

    result->m_coroutine = coroutine;
    Py_INCREF( result->m_coroutine );

    Nuitka_GC_Track( result );

    return (PyObject *)result;
}

#include <structmember.h>

static PyMethodDef Nuitka_Coroutine_methods[] =
{
    { "send",  (PyCFunction)Nuitka_Coroutine_send,  METH_O, NULL },
    { "throw", (PyCFunction)Nuitka_Coroutine_throw, METH_VARARGS, NULL },
    { "close", (PyCFunction)Nuitka_Coroutine_close, METH_NOARGS, NULL },
    { NULL }
};

static PyGetSetDef Nuitka_Coroutine_getsetlist[] =
{
    { (char *)"__name__", (getter)Nuitka_Coroutine_get_name, (setter)Nuitka_Coroutine_set_name, NULL },
    { (char *)"__qualname__", (getter)Nuitka_Coroutine_get_qualname, (setter)Nuitka_Coroutine_set_qualname, NULL },
    { (char *)"cr_await", (getter)Nuitka_Coroutine_get_cr_await, NULL, NULL },
    { (char *)"cr_code",  (getter)Nuitka_Coroutine_get_code, (setter)Nuitka_Coroutine_set_code, NULL },
    { (char *)"cr_frame", (getter)Nuitka_Coroutine_get_frame, (setter)Nuitka_Coroutine_set_frame, NULL },

    { NULL }
};


static PyMemberDef Nuitka_Coroutine_members[] =
{
    { (char *)"cr_running", T_BOOL, offsetof( Nuitka_CoroutineObject, m_running ), READONLY },
    { NULL }
};


static PyAsyncMethods coro_as_async =
{
    (unaryfunc)Nuitka_Coroutine_await,          /* am_await */
    0,                                          /* am_aiter */
    0                                           /* am_anext */
};

PyTypeObject Nuitka_Coroutine_Type = {
    PyVarObject_HEAD_INIT(&PyType_Type, 0)
    "compiled_coroutine",                       /* tp_name */
    sizeof(Nuitka_CoroutineObject),             /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)Nuitka_Coroutine_tp_dealloc,    /* tp_dealloc */
    0,                                          /* tp_print */
    0,                                          /* tp_getattr */
    0,                                          /* tp_setattr */
    &coro_as_async,                             /* tp_as_async */
    (reprfunc)Nuitka_Coroutine_tp_repr,         /* tp_repr */
    0,                                          /* tp_as_number */
    0,                                          /* tp_as_sequence */
    0,                                          /* tp_as_mapping */
    0,                                          /* tp_hash */
    0,                                          /* tp_call */
    0,                                          /* tp_str */
    PyObject_GenericGetAttr,                    /* tp_getattro */
    0,                                          /* tp_setattro */
    0,                                          /* tp_as_buffer */
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC |
        Py_TPFLAGS_HAVE_FINALIZE,               /* tp_flags */
    0,                                          /* tp_doc */
    (traverseproc)Nuitka_Coroutine_tp_traverse, /* tp_traverse */
    0,                                          /* tp_clear */
    0,                                          /* tp_richcompare */
    offsetof( Nuitka_CoroutineObject, m_weakrefs ),  /* tp_weaklistoffset */
    0,                                          /* tp_iter */
    0,                                          /* tp_iternext */
    Nuitka_Coroutine_methods,                   /* tp_methods */
    Nuitka_Coroutine_members,                   /* tp_members */
    Nuitka_Coroutine_getsetlist,                /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    0,                                          /* tp_init */
    0,                                          /* tp_alloc */
    0,                                          /* tp_new */
    0,                                          /* tp_free */
    0,                                          /* tp_is_gc */
    0,                                          /* tp_bases */
    0,                                          /* tp_mro */
    0,                                          /* tp_cache */
    0,                                          /* tp_subclasses */
    0,                                          /* tp_weaklist */
    (destructor)Nuitka_Coroutine_tp_del,        /* tp_del */
    0,                                          /* tp_version_tag */
    /* TODO: Check out the merits of tp_finalize vs. tp_del */
    0,                                          /* tp_finalize */
};

static void Nuitka_CoroutineWrapper_tp_dealloc( Nuitka_CoroutineWrapperObject *cw )
{
    Nuitka_GC_UnTrack( (PyObject *)cw );

    Py_DECREF( cw->m_coroutine );
    cw->m_coroutine = NULL;

    PyObject_GC_Del( cw );
}

static PyObject *Nuitka_CoroutineWrapper_tp_iternext( Nuitka_CoroutineWrapperObject *cw )
{
    return Nuitka_Coroutine_send( cw->m_coroutine, Py_None );
}

static int Nuitka_CoroutineWrapper_tp_traverse( Nuitka_CoroutineWrapperObject *cw, visitproc visit, void *arg )
{
    Py_VISIT( (PyObject *)cw->m_coroutine );
    return 0;
}

static PyObject *Nuitka_CoroutineWrapper_send( Nuitka_CoroutineWrapperObject *cw, PyObject *arg )
{
    return Nuitka_Coroutine_send( cw->m_coroutine, arg );
}

static PyObject *Nuitka_CoroutineWrapper_throw( Nuitka_CoroutineWrapperObject *cw, PyObject *args )
{
    return Nuitka_Coroutine_throw( cw->m_coroutine, args );
}

static PyObject *Nuitka_CoroutineWrapper_close( Nuitka_CoroutineWrapperObject *cw, PyObject *args )
{
    return Nuitka_Coroutine_close( cw->m_coroutine, args );
}

static PyMethodDef Nuitka_CoroutineWrapper_methods[] =
{
    { "send",  (PyCFunction)Nuitka_CoroutineWrapper_send,  METH_O, NULL },
    { "throw", (PyCFunction)Nuitka_CoroutineWrapper_throw, METH_VARARGS, NULL },
    { "close", (PyCFunction)Nuitka_CoroutineWrapper_close, METH_NOARGS, NULL },
    { NULL }
};

PyTypeObject Nuitka_CoroutineWrapper_Type = {
    PyVarObject_HEAD_INIT(&PyType_Type, 0)
    "compiled_coroutine_wrapper",
    sizeof(Nuitka_CoroutineWrapperObject),      /* tp_basicsize */
    0,                                          /* tp_itemsize */
    (destructor)Nuitka_CoroutineWrapper_tp_dealloc,           /* tp_dealloc */
    0,                                          /* tp_print */
    0,                                          /* tp_getattr */
    0,                                          /* tp_setattr */
    0,                                          /* tp_as_async */
    0,                                          /* tp_repr */
    0,                                          /* tp_as_number */
    0,                                          /* tp_as_sequence */
    0,                                          /* tp_as_mapping */
    0,                                          /* tp_hash */
    0,                                          /* tp_call */
    0,                                          /* tp_str */
    PyObject_GenericGetAttr,                    /* tp_getattro */
    0,                                          /* tp_setattro */
    0,                                          /* tp_as_buffer */
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,    /* tp_flags */
    0,                                          /* tp_doc */
    (traverseproc)Nuitka_CoroutineWrapper_tp_traverse,        /* tp_traverse */
    0,                                          /* tp_clear */
    0,                                          /* tp_richcompare */
    0,                                          /* tp_weaklistoffset */
    PyObject_SelfIter,                          /* tp_iter */
    (iternextfunc)Nuitka_CoroutineWrapper_tp_iternext,        /* tp_iternext */
    Nuitka_CoroutineWrapper_methods,                       /* tp_methods */
    0,                                          /* tp_members */
    0,                                          /* tp_getset */
    0,                                          /* tp_base */
    0,                                          /* tp_dict */
    0,                                          /* tp_descr_get */
    0,                                          /* tp_descr_set */
    0,                                          /* tp_dictoffset */
    0,                                          /* tp_init */
    0,                                          /* tp_alloc */
    0,                                          /* tp_new */
    PyObject_Del,                               /* tp_free */
};
