/*
 * query_executor.h — WHERE filter + SELECT projection
 */

#ifndef CSVQL_QUERY_EXECUTOR_H
#define CSVQL_QUERY_EXECUTOR_H

#include "csv_parser.h"

typedef enum {
    OP_EQ = 0, OP_NEQ, OP_GT, OP_GTE, OP_LT, OP_LTE, OP_LIKE
} Operator;

typedef struct {
    char     *column;
    Operator  op;
    char     *value;     /* raw string — cast at runtime */
} WhereClause;

/**
 * Apply SELECT + WHERE on a table.
 * select_cols == NULL means SELECT *
 * where       == NULL means no filter
 * Returns a new CsvTable; caller must csv_free() it.
 */
CsvTable *execute_select(CsvTable    *table,
                          char       **select_cols,
                          int          select_count,
                          WhereClause *where);

/**
 * Apply LIMIT + OFFSET on a table (creates a new table).
 */
CsvTable *execute_limit(CsvTable *table, int limit, int offset);

#endif