/*
 * aggregations.c — GROUP BY engine + ORDER BY sort
 */

#include "aggregations.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <float.h>

/* ─── Helpers ───────────────────────────────────────────────────────── */

static int col_idx(CsvTable *t, const char *name) {
    for (int i = 0; i < t->col_count; i++)
        if (strcmp(t->headers[i], name) == 0) return i;
    return -1;
}

static double cell_to_double(CsvTable *t, int row, int col) {
    CellValue *v = &t->rows[row][col];
    switch (t->col_types[col]) {
        case COL_INT:   return (double)v->i;
        case COL_FLOAT: return v->f;
        default:        return 0.0;
    }
}

static char *cell_to_str(CsvTable *t, int row, int col) {
    static char buf[64];
    CellValue *v = &t->rows[row][col];
    switch (t->col_types[col]) {
        case COL_INT:   snprintf(buf, sizeof(buf), "%ld",  v->i); return buf;
        case COL_FLOAT: snprintf(buf, sizeof(buf), "%.6g", v->f); return buf;
        default:        return v->s ? v->s : "";
    }
}

/* ─── Group key (concatenation of group col values) ─────────────────── */

static char *make_group_key(CsvTable *t, int row, int *gcols, int gcnt) {
    char key[4096] = "";
    for (int i = 0; i < gcnt; i++) {
        if (i > 0) strncat(key, "\x1F", sizeof(key) - strlen(key) - 1);
        strncat(key, cell_to_str(t, row, gcols[i]),
                sizeof(key) - strlen(key) - 1);
    }
    return strdup(key);
}

/* ─── Hash-map bucket ───────────────────────────────────────────────── */

typedef struct GroupEntry {
    char   *key;
    double *agg_vals;   /* running accumulators */
    long   *agg_counts; /* for AVG              */
    int     first_row;  /* to copy group key values */
    struct GroupEntry *next;
} GroupEntry;

#define BUCKET_COUNT 4096

typedef struct {
    GroupEntry *buckets[BUCKET_COUNT];
    int         entry_count;
} GroupMap;

static unsigned int hash_key(const char *s) {
    unsigned int h = 5381;
    while (*s) h = h * 33 ^ (unsigned char)*s++;
    return h % BUCKET_COUNT;
}

static GroupEntry *map_get_or_create(GroupMap *m, const char *key,
                                      int agg_count, int first_row) {
    unsigned int h = hash_key(key);
    for (GroupEntry *e = m->buckets[h]; e; e = e->next)
        if (strcmp(e->key, key) == 0) return e;

    /* New entry */
    GroupEntry *e = calloc(1, sizeof(GroupEntry));
    e->key        = strdup(key);
    e->agg_vals   = calloc(agg_count, sizeof(double));
    e->agg_counts = calloc(agg_count, sizeof(long));
    e->first_row  = first_row;
    /* Init MIN to +inf, MAX to -inf */
    for (int i = 0; i < agg_count; i++) {
        e->agg_vals[i] = 0.0;
    }
    e->next          = m->buckets[h];
    m->buckets[h]    = e;
    m->entry_count++;
    return e;
}

/* ─── GROUP BY ──────────────────────────────────────────────────────── */

CsvTable *execute_groupby(CsvTable    *table,
                           char       **group_cols,
                           int          group_count,
                           Aggregation *aggs,
                           int          agg_count) {
    if (!table || !aggs || agg_count == 0) return NULL;

    /* Resolve group column indices */
    int *gcols = malloc(group_count * sizeof(int));
    for (int i = 0; i < group_count; i++) {
        gcols[i] = col_idx(table, group_cols[i]);
    }

    /* Resolve agg column indices + init tracking */
    int *acols     = malloc(agg_count * sizeof(int));
    double *mins   = malloc(agg_count * sizeof(double));
    double *maxs   = malloc(agg_count * sizeof(double));
    for (int i = 0; i < agg_count; i++) {
        acols[i] = col_idx(table, aggs[i].column);
        mins[i]  =  DBL_MAX;
        maxs[i]  = -DBL_MAX;
    }

    /* Build group map */
    GroupMap *map = calloc(1, sizeof(GroupMap));

    for (int r = 0; r < table->row_count; r++) {
        char *key = make_group_key(table, r, gcols, group_count);
        GroupEntry *e = map_get_or_create(map, key, agg_count, r);
        free(key);

        for (int a = 0; a < agg_count; a++) {
            int ci = acols[a];
            if (ci < 0) { e->agg_counts[a]++; continue; }
            double v = cell_to_double(table, r, ci);
            switch (aggs[a].type) {
                case AGG_SUM:
                case AGG_AVG:   e->agg_vals[a] += v; e->agg_counts[a]++; break;
                case AGG_COUNT: e->agg_counts[a]++; break;
                case AGG_MIN:   if (v < e->agg_vals[a] || e->agg_counts[a] == 0) e->agg_vals[a] = v;
                                e->agg_counts[a]++; break;
                case AGG_MAX:   if (v > e->agg_vals[a] || e->agg_counts[a] == 0) e->agg_vals[a] = v;
                                e->agg_counts[a]++; break;
            }
        }
    }

    /* Build output table */
    int out_cols = group_count + agg_count;
    char    **hdrs  = malloc(out_cols * sizeof(char *));
    ColType  *types = malloc(out_cols * sizeof(ColType));

    for (int i = 0; i < group_count; i++) {
        int ci    = gcols[i];
        hdrs[i]   = strdup(ci >= 0 ? table->headers[ci] : group_cols[i]);
        types[i]  = ci >= 0 ? table->col_types[ci] : COL_STRING;
    }
    for (int i = 0; i < agg_count; i++) {
        const char *alias = aggs[i].alias ? aggs[i].alias : aggs[i].column;
        hdrs[group_count + i]  = strdup(alias);
        types[group_count + i] = COL_FLOAT;
    }

    CsvTable *out = calloc(1, sizeof(CsvTable));
    out->col_count = out_cols;
    out->headers   = hdrs;
    out->col_types = types;
    out->rows      = malloc(map->entry_count * sizeof(CellValue *));
    out->row_count = 0;

    for (int b = 0; b < BUCKET_COUNT; b++) {
        for (GroupEntry *e = map->buckets[b]; e; ) {
            CellValue *row = malloc(out_cols * sizeof(CellValue));

            /* Copy group key values from first_row */
            for (int i = 0; i < group_count; i++) {
                int ci = gcols[i];
                if (ci >= 0) {
                    CellValue v = table->rows[e->first_row][ci];
                    if (table->col_types[ci] == COL_STRING)
                        row[i].s = strdup(v.s);
                    else
                        row[i] = v;
                } else {
                    row[i].s = strdup("");
                }
            }

            /* Aggregation results */
            for (int a = 0; a < agg_count; a++) {
                double res = 0.0;
                switch (aggs[a].type) {
                    case AGG_SUM:   res = e->agg_vals[a]; break;
                    case AGG_AVG:   res = e->agg_counts[a] ? e->agg_vals[a] / e->agg_counts[a] : 0; break;
                    case AGG_COUNT: res = (double)e->agg_counts[a]; break;
                    case AGG_MIN:
                    case AGG_MAX:   res = e->agg_vals[a]; break;
                }
                row[group_count + a].f = res;
            }

            out->rows[out->row_count++] = row;

            GroupEntry *next = e->next;
            free(e->key);
            free(e->agg_vals);
            free(e->agg_counts);
            free(e);
            e = next;
        }
    }

    free(map);
    free(gcols);
    free(acols);
    free(mins);
    free(maxs);
    return out;
}

/* ─── ORDER BY ──────────────────────────────────────────────────────── */

static CsvTable *_sort_table;
static int       _sort_col;
static int       _sort_desc;

static int cmp_rows(const void *a, const void *b) {
    CsvTable *t = _sort_table;
    CellValue *ra = *(CellValue **)a;
    CellValue *rb = *(CellValue **)b;
    int        sign = _sort_desc ? -1 : 1;

    switch (t->col_types[_sort_col]) {
        case COL_INT:
            return sign * ((ra[_sort_col].i > rb[_sort_col].i) -
                           (ra[_sort_col].i < rb[_sort_col].i));
        case COL_FLOAT:
            return sign * ((ra[_sort_col].f > rb[_sort_col].f) -
                           (ra[_sort_col].f < rb[_sort_col].f));
        default:
            return sign * strcmp(ra[_sort_col].s ? ra[_sort_col].s : "",
                                 rb[_sort_col].s ? rb[_sort_col].s : "");
    }
}

void execute_orderby(CsvTable *table, const char *col, int descending) {
    if (!table || table->row_count == 0) return;
    int ci = col_idx(table, col);
    if (ci < 0) return;
    _sort_table = table;
    _sort_col   = ci;
    _sort_desc  = descending;
    qsort(table->rows, table->row_count, sizeof(CellValue *), cmp_rows);
}