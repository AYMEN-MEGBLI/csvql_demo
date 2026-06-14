/*
 * aggregations.h — GROUP BY + SUM/COUNT/AVG/MIN/MAX
 */

#ifndef CSVQL_AGGREGATIONS_H
#define CSVQL_AGGREGATIONS_H

#include "csv_parser.h"

typedef enum {
    AGG_SUM = 0,
    AGG_COUNT,
    AGG_AVG,
    AGG_MIN,
    AGG_MAX
} AggType;

typedef struct {
    AggType  type;
    char    *column;    /* source column  */
    char    *alias;     /* output name    */
} Aggregation;

/**
 * Execute GROUP BY with aggregations.
 * group_cols  — columns to group by
 * aggs        — aggregation specs
 * Returns a new CsvTable; caller must csv_free() it.
 */
CsvTable *execute_groupby(CsvTable    *table,
                           char       **group_cols,
                           int          group_count,
                           Aggregation *aggs,
                           int          agg_count);

/**
 * ORDER BY a column (in place sort on existing table rows).
 */
void execute_orderby(CsvTable *table, const char *col, int descending);

#endif