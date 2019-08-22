# Reference tables

## Compiler ids

These are return values of the `get_id` (Compiler family) and
`get_argument_syntax` (Argument syntax) method in a compiler object.

| Value     | Compiler family                  | Argument syntax                |
| -----     | ---------------                  | ---------------                |
| gcc       | The GNU Compiler Collection      | gcc                            |
| clang     | The Clang compiler               | gcc                            |
| msvc      | Microsoft Visual Studio          | msvc                           |
| intel     | Intel compiler                   | msvc on windows, otherwise gcc |
| llvm      | LLVM-based compiler (Swift, D)   |                                |
| mono      | Xamarin C# compiler              |                                |
| dmd       | D lang reference compiler        |                                |
| rustc     | Rust compiler                    |                                |
| valac     | Vala compiler                    |                                |
| pathscale | The Pathscale Fortran compiler   |                                |
| pgi       | The Portland Fortran compiler    |                                |
| sun       | Sun Fortran compiler             |                                |
| g95       | The G95 Fortran compiler         |                                |
| open64    | The Open64 Fortran Compiler      |                                |
| nagfor    | The NAG Fortran compiler         |                                |
| lcc       | Elbrus C/C++/Fortran Compiler    |                                |
| arm       | ARM compiler                     |                                |
| armclang  | ARMCLANG compiler                |                                |
| ccrx      | Renesas RX Family C/C++ compiler |                                |
| clang-cl  | The Clang compiler (MSVC compatible driver) | msvc                |

## Script environment variables

| Value               | Comment                         |
| -----               | -------                         |
| MESON_SOURCE_ROOT   | Absolute path to the source dir |
| MESON_BUILD_ROOT    | Absolute path to the build dir  |
| MESONINTROSPECT     | Command to run to run the introspection command, may be of the form `python /path/to/meson introspect`, user is responsible for splitting the path if necessary. |
| MESON_SUBDIR        | Current subdirectory, only set for `run_command` |
| MESON_DIST_ROOT     | Points to the root of the staging directory, only set when running `dist` scripts |


## CPU families

These are returned by the `cpu_family` method of `build_machine`,
`host_machine` and `target_machine`. For cross compilation they are
set in the cross file.

| Value               | Comment                         |
| -----               | -------                         |
| x86                 | 32 bit x86 processor  |
| x86_64              | 64 bit x86 processor  |
| ia64                | Itanium processor     |
| arm                 | 32 bit ARM processor  |
| arc                 | 32 bit ARC processor  |
| aarch64             | 64 bit ARM processor  |
| mips                | 32 bit MIPS processor |
| mips64              | 64 bit MIPS processor |
| ppc                 | 32 bit PPC processors |
| ppc64               | 64 bit PPC processors |
| e2k                 | MCST Elbrus processor |
| parisc              | HP PA-RISC processor  |
| riscv32             | 32 bit RISC-V Open ISA|
| riscv64             | 64 bit RISC-V Open ISA|
| sparc               | 32 bit SPARC          |
| sparc64             | SPARC v9 processor    |
| s390x               | IBM zSystem s390x     |
| rx                  | Renesas RX 32 bit MCU |

Any cpu family not listed in the above list is not guaranteed to
remain stable in future releases.

Those porting from autotools should note that meson does not add
endianness to the name of the cpu_family. For example, autotools
will call little endian PPC64 "ppc64le", meson will not, you must
also check the `.endian()` value of the machine for this information.

## Operating system names

These are provided by the `.system()` method call.

| Value               | Comment                         |
| -----               | -------                         |
| linux               | |
| darwin              | Either OSX or iOS |
| windows             | Any version of Windows |
| cygwin              | The Cygwin environment for Windows |
| haiku               | |
| freebsd             | FreeBSD and its derivatives |
| dragonfly           | DragonFly BSD |
| netbsd              | |
| gnu                 | GNU Hurd |

Any string not listed above is not guaranteed to remain stable in
future releases.


## Language arguments parameter names

These are the parameter names for passing language specific arguments to your build target.

| Language      | Parameter name |
| -----         | ----- |
| C             | c_args |
| C++           | cpp_args |
| C#            | cs_args |
| D             | d_args |
| Fortran       | fortran_args |
| Java          | java_args |
| Objective C   | objc_args |
| Objective C++ | objcpp_args |
| Rust          | rust_args |
| Vala          | vala_args |


## Function Attributes

These are the parameters names that are supported using
`compiler.has_function_attribute()` or
`compiler.get_supported_function_attributes()`

### GCC `__attribute__`

These values are supported using the GCC style `__attribute__` annotations,
which are supported by GCC, Clang, and other compilers.


| Name                 |
|----------------------|
| alias                |
| aligned              |
| alloc_size           |
| always_inline        |
| artificial           |
| cold                 |
| const                |
| constructor          |
| constructor_priority |
| deprecated           |
| destructor           |
| error                |
| externally_visible   |
| fallthrough          |
| flatten              |
| format               |
| format_arg           |
| gnu_inline           |
| hot                  |
| ifunc                |
| malloc               |
| noclone              |
| noinline             |
| nonnull              |
| noreturn             |
| nothrow              |
| optimize             |
| packed               |
| pure                 |
| returns_nonnull      |
| unused               |
| used                 |
| visibility           |
| warning              |
| warn_unused_result   |
| weak                 |
| weakreaf             |

### MSVC __declspec

These values are supported using the MSVC style `__declspec` annotation,
which are supported by MSVC, GCC, Clang, and other compilers.

| Name                 |
|----------------------|
| dllexport            |
| dllimport            |
