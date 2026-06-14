/*
 * query_executor.c — WHERE filter and SELECT projection
 */

#include "query_executor.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#ifndef _WIN32
#include <fnmatch.h>
#else
/* simple glob-like match fallback for Windows */
static int fnmatch(const char *pat, const char *s, int flags) {
    (void)flags;
    return strstr(s, pat) == NULL;
}
#endif

/* ─── Find column index by name ────────────────────────────────────── */
static int col_index(CsvTable *t, const char *name) {
    for (int i = 0; i < t->col_count; i++)
        if (strcmp(t->headers[i], name) == 0) return i;
    return -1;
}

/* ─── WHERE evaluation ──────────────────────────────────────────────── */
static int eval_where(CsvTable *t, int row, WhereClause *w) {
    int ci = col_index(t, w->column);
    if (ci < 0) return 0;

    CellValue *cell = &t->rows[row][ci];
    ColType    type =  t->col_types[ci];

    if (w->op == OP_LIKE) {
        /* LIKE only on strings; % → * for fnmatch */
        if (type != COL_STRING) return 0;
        char pat[512];
        strncpy(pat, w->value, sizeof(pat) - 1);
        for (char *p = pat; *p; p++) if (*p == '%') *p = '*';
        return fnmatch(pat, cell->s, 0) == 0;
    }

    double lhs, rhs;
    if (type == COL_INT)   lhs = (double)cell->i;
    else if (type == COL_FLOAT) lhs = cell->f;
    else {
        /* string comparison */
        int cmp = strcmp(cell->s, w->value);
        switch (w->op) {
            case OP_EQ:  return cmp == 0;
            case OP_NEQ: return cmp != 0;
            case OP_GT:  return cmp >  0;
            case OP_GTE: return cmp >= 0;
            case OP_LT:  return cmp <  0;
            case OP_LTE: return cmp <= 0;
            default:     return 0;
        }
    }
    rhs = atof(w->value);
    switch (w->op) {
        case OP_EQ:  return lhs == rhs;
        case OP_NEQ: return lhs != rhs;
        case OP_GT:  return lhs >  rhs;
        case OP_GTE: return lhs >= rhs;
        case OP_LT:  return lhs <  rhs;
        case OP_LTE: return lhs <= rhs;
        default:     return 0;
    }
}

/* ─── Copy a single cell (deep copy for strings) ────────────────────── */
static CellValue copy_cell(CellValue v, ColType t) {
    if (t == COL_STRING) {
        CellValue out;
        out.s = v.s ? strdup(v.s) : strdup("");
        return out;
    }
    return v;
}

/* ─── Public API ────────────────────────────────────────────────────── */

CsvTable *execute_select(CsvTable    *table,
                          char       **select_cols,
                          int          select_count,
                          WhereClause *where) {
    if (!table) return NULL;

    /* Resolve output columns */
    int *out_idx  = NULL;
    int  out_cols = 0;

    if (!select_cols || select_count == 0) {
        /* SELECT * */
        out_cols = table->col_count;
        out_idx  = malloc(out_cols * sizeof(int));
        for (int i = 0; i < out_cols; i++) out_idx[i] = i;
    } else {
        out_cols = select_count;
        out_idx  = malloc(out_cols * sizeof(int));
        for (int i = 0; i < out_cols; i++) {
            out_idx[i] = col_index(table, select_cols[i]);
        }
    }

    /* Build output table */
    char    **new_headers = malloc(out_cols * sizeof(char *));
    ColType  *new_types   = malloc(out_cols * sizeof(ColType));
    for (int i = 0; i < out_cols; i++) {
        int ci = out_idx[i];
        new_headers[i] = ci >= 0 ? strdup(table->headers[ci]) : strdup("NULL");
        new_types[i]   = ci >= 0 ? table->col_types[ci] : COL_STRING;
    }

    CsvTable *out = calloc(1, sizeof(CsvTable));
    out->col_count = out_cols;
    out->headers   = new_headers;
    out->col_types = new_types;
    out->rows      = malloc(table->row_count * sizeof(CellValue *));
    out->row_count = 0;

    for (int r = 0; r < table->row_count; r++) {
        if (where && !eval_where(table, r, where)) continue;

        CellValue *row = malloc(out_cols * sizeof(CellValue));
        for (int i = 0; i < out_cols; i++) {
            int ci = out_idx[i];
            if (ci >= 0)
                row[i] = copy_cell(table->rows[r][ci], table->col_types[ci]);
            else
                row[i].s = strdup("");
        }
        out->rows[out->row_count++] = row;
    }

    free(out_idx);
    return out;
}

CsvTable *execute_limit(CsvTable *table, int limit, int offset) {
    if (!table) return NULL;
    if (offset >= table->row_count) limit = 0;

    int start = offset < table->row_count ? offset : table->row_count;
    int end   = start + limit;
    if (end > table->row_count) end = table->row_count;

    CsvTable *out = calloc(1, sizeof(CsvTable));
    out->col_count = table->col_count;
    out->headers   = malloc(table->col_count * sizeof(char *));
    out->col_types = malloc(table->col_count * sizeof(ColType));
    for (int c = 0; c < table->col_count; c++) {
        out->headers[c]   = strdup(table->headers[c]);
        out->col_types[c] = table->col_types[c];
    }
    int n   = end - start;
    out->rows      = malloc(n * sizeof(CellValue *));
    out->row_count = 0;

    for (int r = start; r < end; r++) {
        CellValue *row = malloc(table->col_count * sizeof(CellValue));
        for (int c = 0; c < table->col_count; c++)
            row[c] = copy_cell(table->rows[r][c], table->col_types[c]);
        out->rows[out->row_count++] = row;
    }
    return out;
}