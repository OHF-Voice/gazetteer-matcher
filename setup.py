"""Build the optional native fuzzy-scoring accelerator."""

import os

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext


class BuildExt(build_ext):
    """Select the C++17 flag understood by the active compiler."""

    def build_extensions(self) -> None:
        option = "/std:c++17" if self.compiler.compiler_type == "msvc" else "-std=c++17"
        for extension in self.extensions:
            extension.extra_compile_args.append(option)
        super().build_extensions()


setup(
    ext_modules=[
        Extension(
            "gazetteer_matcher._fuzzy_native",
            ["src/gazetteer_matcher/_fuzzy_native.cpp"],
            language="c++",
            define_macros=[("Py_LIMITED_API", "0x030B0000")],
            py_limited_api=True,
            optional=os.environ.get("CIBUILDWHEEL") != "1",
        )
    ],
    cmdclass={"build_ext": BuildExt},
    options={"bdist_wheel": {"py_limited_api": "cp311"}},
)
