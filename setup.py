from setuptools import setup, find_packages, Extension

core_module = Extension(
    "csvql._core",
    sources=[
        "src/python_bindings.c",
        "src/csv_parser.c",
        "src/aggregations.c",
        "src/query_executor.c",
        "src/join_engine.c",
    ],
    include_dirs=["src"],
)

setup(
    name="csvql",
    version="0.1.0",
    packages=find_packages(),
    ext_modules=[core_module],
    python_requires=">=3.9",
)
