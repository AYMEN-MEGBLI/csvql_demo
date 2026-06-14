/*
 * csv_parser.h — Fast CSV reader using mmap + chunk-based parsing
 * Part of csvql — SQL queries on CSV files
 * License: MIT
 */

#ifndef CSVQL_CSV_PARSER_H
#define CSVQL_CSV_PARSER_H

#include <stddef.h>

/* ─── Column type enum ─────────────────────────────────────────────── */
typedef enum {
    COL_STRING = 0,
    COL_INT    = 1,
    COL_FLOAT  = 2
} ColType;

/* ─── A single parsed value ────────────────────────────────────────── */
typedef union {
    char   *s;   /* COL_STRING */
    long    i;   /* COL_INT    */
    double  f;   /* COL_FLOAT  */
} CellValue;

/* ─── One table (headers + rows) ───────────────────────────────────── */
typedef struct {
    char      **headers;      /* column names          [col_count] */
    ColType    *col_types;    /* inferred type per col [col_count] */
    CellValue **rows;         /* rows[row][col]                    */
    int         row_count;
    int         col_count;
} CsvTable;

/* ─── Public API ────────────────────────────────────────────────────── */

/**
 * Parse an entire CSV file into a CsvTable.
 * Uses mmap for files > 10 MB, fread otherwise.
 */
CsvTable *csv_parse_file(const char *filepath);

/**
 * Parse only the bytes [start_byte, end_byte) of a CSV file.
 * headers must be pre-read via csv_read_headers().
 * Designed for parallel chunk processing.
 */
CsvTable *csv_parse_chunk(const char *filepath,
                           long        start_byte,
                           long        end_byte,
                           char      **headers,
                           int         col_count);

/**
 * Read only the header row of a CSV file.
 * Returns a NULL-terminated array of strings; caller owns memory.
 */
char **csv_read_headers(const char *filepath, int *col_count_out);

/**
 * Return the byte offset of the first data row (after header newline).
 */
long csv_header_end(const char *filepath);

/**
 * Scan forward from `approx_pos` to find the next newline byte offset.
 * Used to split the file cleanly between threads.
 */
long csv_find_newline(const char *filepath, long approx_pos);

/**
 * Free all memory owned by a CsvTable.
 */
void csv_free(CsvTable *table);

/**
 * Merge an array of partial CsvTables into one (preserves row order).
 * Frees each partial table after merging.
 */
CsvTable *csv_merge(CsvTable **tables, int count);

#endif /* CSVQL_CSV_PARSER_H */