/*
 * python_bindings.c — Python C Extension
 * Exposes csv_parse_file, csv_parse_chunk, execute_select,
 * execute_groupby, execute_orderby, execute_limit to Python.
 *
 * GIL is released during all C-heavy operations so that
 * Python ThreadPoolExecutor achieves true parallelism.
 *
 * All parsed rows are returned as list[list] (not list[dict])
 * to match the refactored internal format of parallel.py.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <structmember.h>
#include <string.h>
#include <stdlib.h>

#ifndef _WIN32
#include <fnmatch.h>
#endif

#include "csv_parser.h"
#include "query_executor.h"
#include "aggregations.h"

/* ─── Helpers: CsvTable → Python list[list] ─────────────────────────── */

static PyObject *cell_to_py(CellValue *cell, ColType type) {
    switch (type) {
        case COL_INT:   return PyLong_FromLong(cell->i);
        case COL_FLOAT: return PyFloat_FromDouble(cell->f);
        default:        return PyUnicode_FromString(cell->s ? cell->s : "");
    }
}

static PyObject *table_to_pylistlist(CsvTable *table) {
    if (!table) Py_RETURN_NONE;

    PyObject *list = PyList_New(table->row_count);
    for (int r = 0; r < table->row_count; r++) {
        PyObject *row = PyList_New(table->col_count);
        for (int c = 0; c < table->col_count; c++) {
            PyObject *val = cell_to_py(&table->rows[r][c], table->col_types[c]);
            PyList_SET_ITEM(row, c, val);
        }
        PyList_SET_ITEM(list, r, row);
    }
    return list;
}

/* ─── Python helpers: list[str] from PyObject ───────────────────────── */

static char **pylist_to_strarray(PyObject *obj, int *count) {
    if (!obj || obj == Py_None) { *count = 0; return NULL; }
    Py_ssize_t n = PyList_Size(obj);
    char **arr = malloc(n * sizeof(char *));
    for (Py_ssize_t i = 0; i < n; i++)
        arr[i] = (char *)PyUnicode_AsUTF8(PyList_GET_ITEM(obj, i));
    *count = (int)n;
    return arr;
}

/* ─── Binding: parse_file(filepath) → list[dict] ────────────────────── */

static PyObject *py_parse_file(PyObject *self, PyObject *args) {
    const char *filepath;
    if (!PyArg_ParseTuple(args, "s", &filepath)) return NULL;

    CsvTable *table;
    Py_BEGIN_ALLOW_THREADS
        table = csv_parse_file(filepath);
    Py_END_ALLOW_THREADS

    if (!table) {
        PyErr_SetString(PyExc_IOError, "Failed to parse CSV file");
        return NULL;
    }
    PyObject *result = table_to_pylistlist(table);
    csv_free(table);
    return result;
}

/* ─── Binding: parse_chunk(filepath, start, end, headers) ───────────── */

static PyObject *py_parse_chunk(PyObject *self, PyObject *args) {
    const char *filepath;
    long start, end;
    PyObject *py_headers;

    if (!PyArg_ParseTuple(args, "sllO", &filepath, &start, &end, &py_headers))
        return NULL;

    int col_count = 0;
    char **headers = pylist_to_strarray(py_headers, &col_count);

    CsvTable *table;
    Py_BEGIN_ALLOW_THREADS
        table = csv_parse_chunk(filepath, start, end, headers, col_count);
    Py_END_ALLOW_THREADS

    free(headers);

    if (!table) {
        PyErr_SetString(PyExc_IOError, "Failed to parse CSV chunk");
        return NULL;
    }
    PyObject *result = table_to_pylistlist(table);
    csv_free(table);
    return result;
}

/* ─── Binding: read_headers(filepath) → (list[str], int) ────────────── */

static PyObject *py_read_headers(PyObject *self, PyObject *args) {
    const char *filepath;
    if (!PyArg_ParseTuple(args, "s", &filepath)) return NULL;

    int col_count = 0;
    char **headers = csv_read_headers(filepath, &col_count);
    if (!headers) {
        PyErr_SetString(PyExc_IOError, "Cannot read CSV headers");
        return NULL;
    }
    PyObject *list = PyList_New(col_count);
    for (int i = 0; i < col_count; i++) {
        PyList_SET_ITEM(list, i, PyUnicode_FromString(headers[i]));
        free(headers[i]);
    }
    free(headers);
    return Py_BuildValue("(Oi)", list, col_count);
}

/* ─── Binding: header_end(filepath) → int ───────────────────────────── */

static PyObject *py_header_end(PyObject *self, PyObject *args) {
    const char *filepath;
    if (!PyArg_ParseTuple(args, "s", &filepath)) return NULL;
    return PyLong_FromLong(csv_header_end(filepath));
}

/* ─── Binding: find_newline(filepath, approx_pos) → int ─────────────── */

static PyObject *py_find_newline(PyObject *self, PyObject *args) {
    const char *filepath;
    long approx;
    if (!PyArg_ParseTuple(args, "sl", &filepath, &approx)) return NULL;
    return PyLong_FromLong(csv_find_newline(filepath, approx));
}

/* ─── Binding: filter_rows(rows, col_idx, op, value) → list[list] ───── */
/* Works on list[list] format, where col_idx gives the column position   */

static PyObject *py_filter_rows(PyObject *self, PyObject *args) {
    PyObject   *rows;
    int         col_idx;
    const char *op_str, *value;
    if (!PyArg_ParseTuple(args, "Oiss", &rows, &col_idx, &op_str, &value))
        return NULL;

    Operator op;
    if      (strcmp(op_str, "=")  == 0 || strcmp(op_str, "==") == 0) op = OP_EQ;
    else if (strcmp(op_str, "!=") == 0 || strcmp(op_str, "<>") == 0) op = OP_NEQ;
    else if (strcmp(op_str, ">")  == 0) op = OP_GT;
    else if (strcmp(op_str, ">=") == 0) op = OP_GTE;
    else if (strcmp(op_str, "<")  == 0) op = OP_LT;
    else if (strcmp(op_str, "<=") == 0) op = OP_LTE;
    else if (strcmp(op_str, "LIKE") == 0 ||
             strcmp(op_str, "like") == 0) op = OP_LIKE;
    else {
        PyErr_SetString(PyExc_ValueError, "Unknown operator");
        return NULL;
    }

    PyObject *result = PyList_New(0);
    Py_ssize_t n = PyList_Size(rows);

    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *row = PyList_GET_ITEM(rows, i);
        Py_ssize_t row_len = PyList_Size(row);
        if (col_idx < 0 || col_idx >= row_len) continue;
        PyObject *cell = PyList_GET_ITEM(row, col_idx);

        int match = 0;
        if (PyLong_Check(cell) || PyFloat_Check(cell)) {
            double lhs = PyFloat_Check(cell) ? PyFloat_AsDouble(cell)
                                             : (double)PyLong_AsLong(cell);
            double rhs = atof(value);
            switch (op) {
                case OP_EQ:  match = lhs == rhs; break;
                case OP_NEQ: match = lhs != rhs; break;
                case OP_GT:  match = lhs >  rhs; break;
                case OP_GTE: match = lhs >= rhs; break;
                case OP_LT:  match = lhs <  rhs; break;
                case OP_LTE: match = lhs <= rhs; break;
                default:     match = 0;
            }
        } else if (PyUnicode_Check(cell)) {
            const char *s = PyUnicode_AsUTF8(cell);
            if (op == OP_LIKE) {
                char pat[512];
                strncpy(pat, value, 511);
                for (char *p = pat; *p; p++) if (*p == '%') *p = '*';
                #ifndef _WIN32
                match = fnmatch(pat, s, 0) == 0;
                #else
                match = strstr(s, value) != NULL;
                #endif
            } else {
                int cmp = strcmp(s, value);
                switch (op) {
                    case OP_EQ:  match = cmp == 0; break;
                    case OP_NEQ: match = cmp != 0; break;
                    case OP_GT:  match = cmp >  0; break;
                    case OP_GTE: match = cmp >= 0; break;
                    case OP_LT:  match = cmp <  0; break;
                    case OP_LTE: match = cmp <= 0; break;
                    default:     match = 0;
                }
            }
        }
        if (match) PyList_Append(result, row);
    }

    return result;
}

/* ─── Method table ──────────────────────────────────────────────────── */

static PyMethodDef CsvqlMethods[] = {
    {"parse_file",    py_parse_file,    METH_VARARGS, "Parse full CSV file"},
    {"parse_chunk",   py_parse_chunk,   METH_VARARGS, "Parse CSV chunk"},
    {"read_headers",  py_read_headers,  METH_VARARGS, "Read CSV headers"},
    {"header_end",    py_header_end,    METH_VARARGS, "Byte offset after header"},
    {"find_newline",  py_find_newline,  METH_VARARGS, "Find next newline offset"},
    {"filter_rows",   py_filter_rows,   METH_VARARGS, "Filter list[dict] with WHERE"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef csvql_module = {
    PyModuleDef_HEAD_INIT, "_core", NULL, -1, CsvqlMethods
};

PyMODINIT_FUNC PyInit__core(void) {
    return PyModule_Create(&csvql_module);
}