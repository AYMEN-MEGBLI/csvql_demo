/*
 * csv_parser.c — mmap-based CSV parser with chunk support
 * Handles quoted fields, escaped commas, auto type-detection.
 * Thread-safe: no global mutable state.
 */

#include "csv_parser.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#ifdef _WIN32
  #include <windows.h>
#else
  #include <sys/mman.h>
  #include <sys/stat.h>
  #include <fcntl.h>
  #include <unistd.h>
#endif

#define MMAP_THRESHOLD (10 * 1024 * 1024)   /* 10 MB */
#define INIT_ROW_CAP   1024
#define MAX_FIELD_LEN  4096

/* ─── Internal helpers ─────────────────────────────────────────────── */

static char *str_dup(const char *s) {
    size_t len = strlen(s);
    char *out = malloc(len + 1);
    if (!out) return NULL;
    memcpy(out, s, len + 1);
    return out;
}

static char *str_ndup(const char *s, size_t n) {
    char *out = malloc(n + 1);
    if (!out) return NULL;
    memcpy(out, s, n);
    out[n] = '\0';
    return out;
}

/* Trim leading/trailing whitespace in-place (returns new start) */
static char *str_trim(char *s) {
    while (isspace((unsigned char)*s)) s++;
    char *end = s + strlen(s) - 1;
    while (end > s && isspace((unsigned char)*end)) *end-- = '\0';
    return s;
}

/* ─── Type inference ───────────────────────────────────────────────── */

static ColType infer_type(const char *s) {
    if (!s || *s == '\0') return COL_STRING;
    char *end;
    strtol(s, &end, 10);
    if (*end == '\0') return COL_INT;
    strtod(s, &end);
    if (*end == '\0') return COL_FLOAT;
    return COL_STRING;
}

static CellValue parse_cell(const char *s, ColType type) {
    CellValue v;
    switch (type) {
        case COL_INT:    v.i = strtol(s, NULL, 10); break;
        case COL_FLOAT:  v.f = strtod(s, NULL);     break;
        default:         v.s = str_dup(s);           break;
    }
    return v;
}

/* ─── CSV line tokenizer (handles quoted fields) ───────────────────── */

typedef struct { char **fields; int count; } Fields;

static Fields split_line(const char *line) {
    Fields f;
    f.fields = NULL;
    f.count  = 0;

    int cap = 16;
    f.fields = malloc(cap * sizeof(char *));
    if (!f.fields) return f;

    char buf[MAX_FIELD_LEN];
    int  bi   = 0;
    int  in_q = 0;

    for (const char *p = line; *p && *p != '\n' && *p != '\r'; p++) {
        if (*p == '"') {
            if (in_q && *(p+1) == '"') { buf[bi++] = '"'; p++; }
            else                         in_q = !in_q;
        } else if (*p == ',' && !in_q) {
            buf[bi] = '\0';
            if (f.count >= cap) {
                cap *= 2;
                f.fields = realloc(f.fields, cap * sizeof(char *));
            }
            f.fields[f.count++] = str_dup(str_trim(buf));
            bi = 0;
        } else {
            if (bi < MAX_FIELD_LEN - 1) buf[bi++] = *p;
        }
    }
    /* last field */
    buf[bi] = '\0';
    if (f.count >= cap) {
        cap++;
        f.fields = realloc(f.fields, cap * sizeof(char *));
    }
    f.fields[f.count++] = str_dup(str_trim(buf));
    return f;
}

static void fields_free(Fields *f) {
    for (int i = 0; i < f->count; i++) free(f->fields[i]);
    free(f->fields);
}

/* ─── Table allocation helpers ─────────────────────────────────────── */

static CsvTable *table_alloc(char **headers, ColType *types, int col_count) {
    CsvTable *t = calloc(1, sizeof(CsvTable));
    if (!t) return NULL;
    t->col_count = col_count;
    t->headers   = headers;
    t->col_types = types;
    t->rows      = malloc(INIT_ROW_CAP * sizeof(CellValue *));
    t->row_count = 0;
    return t;
}

static void table_add_row(CsvTable *t, int *cap, CellValue *row) {
    if (t->row_count >= *cap) {
        *cap *= 2;
        t->rows = realloc(t->rows, *cap * sizeof(CellValue *));
    }
    t->rows[t->row_count] = malloc(t->col_count * sizeof(CellValue));
    memcpy(t->rows[t->row_count], row, t->col_count * sizeof(CellValue));
    t->row_count++;
}

/* ─── Core line-by-line parser (shared between full + chunk) ────────── */

static CsvTable *parse_buffer(const char *buf, size_t len,
                               char **ext_headers, ColType *ext_types,
                               int ext_col_count, int skip_header) {
    char **headers   = ext_headers;
    ColType *types   = ext_types;
    int col_count    = ext_col_count;

    const char *p   = buf;
    const char *end = buf + len;

    /* ── Parse header row (only when no external headers) ── */
    if (!headers) {
        const char *nl = memchr(p, '\n', end - p);
        if (!nl) nl = end;

        char *header_line = str_ndup(p, nl - p);
        Fields hf = split_line(header_line);
        free(header_line);

        col_count = hf.count;
        headers   = hf.fields;          /* transfer ownership */
        types     = calloc(col_count, sizeof(ColType));

        p = (nl < end) ? nl + 1 : end;  /* skip past header newline */
    }
    /* When ext_headers is provided, p stays at buf — no header to skip */

    int cap = INIT_ROW_CAP;
    CsvTable *table = table_alloc(headers, types, col_count);
    if (!table) return NULL;

    CellValue *row_buf = malloc(col_count * sizeof(CellValue));

    /* ── First pass: infer types from first 100 rows ── */
    if (!ext_types) {
        const char *scan = p;
        int sample = 0;
        while (scan < end && sample < 100) {
            const char *snl = memchr(scan, '\n', end - scan);
            if (!snl) snl = end;
            char *line = str_ndup(scan, snl - scan);
            Fields f   = split_line(line);
            free(line);
            for (int c = 0; c < col_count && c < f.count; c++) {
                ColType t = infer_type(f.fields[c]);
                if (t > types[c]) types[c] = t;   /* promote: STRING > FLOAT > INT */
            }
            fields_free(&f);
            scan  = snl + 1;
            sample++;
        }
    }

    /* ── Second pass: parse all rows ── */
    while (p < end) {
        const char *line_end = memchr(p, '\n', end - p);
        if (!line_end) line_end = end;

        if (line_end > p) {              /* skip empty lines */
            char *line = str_ndup(p, line_end - p);
            Fields f   = split_line(line);
            free(line);

            if (f.count > 0) {
                for (int c = 0; c < col_count; c++) {
                    const char *val = (c < f.count) ? f.fields[c] : "";
                    row_buf[c] = parse_cell(val, types[c]);
                }
                table_add_row(table, &cap, row_buf);
            }
            fields_free(&f);
        }
        p = line_end + 1;
    }

    free(row_buf);
    return table;
}

/* ─── Public API ────────────────────────────────────────────────────── */

char **csv_read_headers(const char *filepath, int *col_count_out) {
    FILE *f = fopen(filepath, "r");
    if (!f) return NULL;

    char line[65536];
    if (!fgets(line, sizeof(line), f)) { fclose(f); return NULL; }
    fclose(f);

    Fields hf = split_line(line);
    if (col_count_out) *col_count_out = hf.count;
    return hf.fields;
}

long csv_header_end(const char *filepath) {
    FILE *f = fopen(filepath, "rb");
    if (!f) return 0;
    char c;
    long pos = 0;
    while (fread(&c, 1, 1, f) == 1) {
        pos++;
        if (c == '\n') break;
    }
    fclose(f);
    return pos;
}

long csv_find_newline(const char *filepath, long approx_pos) {
    FILE *f = fopen(filepath, "rb");
    if (!f) return approx_pos;
    fseek(f, approx_pos, SEEK_SET);
    char c;
    long pos = approx_pos;
    while (fread(&c, 1, 1, f) == 1) {
        pos++;
        if (c == '\n') break;
    }
    fclose(f);
    return pos;
}

CsvTable *csv_parse_file(const char *filepath) {
#ifndef _WIN32
    int fd = open(filepath, O_RDONLY);
    if (fd < 0) return NULL;
    struct stat st;
    fstat(fd, &st);
    size_t size = st.st_size;

    if (size >= MMAP_THRESHOLD) {
        char *buf = mmap(NULL, size, PROT_READ, MAP_PRIVATE, fd, 0);
        close(fd);
        if (buf == MAP_FAILED) return NULL;
        CsvTable *t = parse_buffer(buf, size, NULL, NULL, 0, 0);
        munmap(buf, size);
        return t;
    }
    close(fd);
#endif
    /* fread fallback (Windows or small files) */
    FILE *f = fopen(filepath, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long file_size = ftell(f);
    rewind(f);
    char *buf = malloc(file_size);
    if (!buf) { fclose(f); return NULL; }
    (void)fread(buf, 1, file_size, f);
    fclose(f);
    CsvTable *t = parse_buffer(buf, file_size, NULL, NULL, 0, 0);
    free(buf);
    return t;
}

CsvTable *csv_parse_chunk(const char *filepath,
                           long start_byte, long end_byte,
                           char **ext_headers, int col_count) {
    FILE *f = fopen(filepath, "rb");
    if (!f) return NULL;

    fseek(f, start_byte, SEEK_SET);
    long chunk_len = end_byte - start_byte;
    char *buf = malloc(chunk_len + 1);
    if (!buf) { fclose(f); return NULL; }
    size_t read = fread(buf, 1, chunk_len, f);
    fclose(f);
    buf[read] = '\0';

    /* Deep-copy headers so csv_free can free them */
    char **headers = malloc(col_count * sizeof(char *));
    for (int i = 0; i < col_count; i++)
        headers[i] = str_dup(ext_headers[i]);

    /* Chunk has no header row — pass pre-read headers */
    ColType *types = calloc(col_count, sizeof(ColType));
    CsvTable *t = parse_buffer(buf, read, headers, types, col_count, 1);
    free(buf);
    return t;
}

void csv_free(CsvTable *table) {
    if (!table) return;
    for (int r = 0; r < table->row_count; r++) {
        for (int c = 0; c < table->col_count; c++) {
            if (table->col_types[c] == COL_STRING)
                free(table->rows[r][c].s);
        }
        free(table->rows[r]);
    }
    free(table->rows);
    for (int c = 0; c < table->col_count; c++) free(table->headers[c]);
    free(table->headers);
    free(table->col_types);
    free(table);
}

CsvTable *csv_merge(CsvTable **tables, int count) {
    if (!tables || count == 0) return NULL;

    /* Count total rows */
    int total = 0;
    for (int i = 0; i < count; i++)
        if (tables[i]) total += tables[i]->row_count;

    CsvTable *out = calloc(1, sizeof(CsvTable));
    CsvTable *first = NULL;
    for (int i = 0; i < count; i++) if (tables[i]) { first = tables[i]; break; }
    if (!first) { free(out); return NULL; }

    out->col_count = first->col_count;
    out->col_types = malloc(out->col_count * sizeof(ColType));
    out->headers   = malloc(out->col_count * sizeof(char *));
    memcpy(out->col_types, first->col_types, out->col_count * sizeof(ColType));
    for (int c = 0; c < out->col_count; c++)
        out->headers[c] = str_dup(first->headers[c]);

    out->rows = malloc(total * sizeof(CellValue *));
    out->row_count = 0;

    for (int i = 0; i < count; i++) {
        CsvTable *t = tables[i];
        if (!t) continue;
        for (int r = 0; r < t->row_count; r++) {
            CellValue *row = malloc(out->col_count * sizeof(CellValue));
            for (int c = 0; c < out->col_count; c++) {
                if (out->col_types[c] == COL_STRING)
                    row[c].s = str_dup(t->rows[r][c].s);
                else
                    row[c] = t->rows[r][c];
            }
            out->rows[out->row_count++] = row;
        }
        csv_free(t);
        tables[i] = NULL;
    }
    return out;
}