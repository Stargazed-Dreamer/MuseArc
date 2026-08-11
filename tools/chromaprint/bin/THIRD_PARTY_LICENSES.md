# Chromaprint 第三方组件许可证声明

本目录下的 DLL 文件用于 Windows 平台的音频指纹生成。各组件的版权与许可证如下。

## 组件清单

| 文件 | 上游项目 | 许可证 |
|------|----------|--------|
| `libchromaprint.dll` | [Chromaprint](https://acoustid.org/chromaprint) | LGPL-2.1-or-later |
| `libfftw3-3.dll` | [FFTW](http://www.fftw.org/) | GPL-2.0-or-later |
| `libfftw3_omp-3.dll` | FFTW (OpenMP) | GPL-2.0-or-later |
| `libfftw3_threads-3.dll` | FFTW (pthreads) | GPL-2.0-or-later |
| `libfftw3f-3.dll` | FFTW (单精度) | GPL-2.0-or-later |
| `libfftw3f_omp-3.dll` | FFTW (单精度, OpenMP) | GPL-2.0-or-later |
| `libfftw3f_threads-3.dll` | FFTW (单精度, pthreads) | GPL-2.0-or-later |
| `libfftw3l-3.dll` | FFTW (长双精度) | GPL-2.0-or-later |
| `libfftw3l_omp-3.dll` | FFTW (长双精度, OpenMP) | GPL-2.0-or-later |
| `libfftw3l_threads-3.dll` | FFTW (长双精度, pthreads) | GPL-2.0-or-later |
| `libgcc_s_seh-1.dll` | GCC runtime | GPL-3.0-or-later WITH GCC-exception-3.1 |
| `libstdc++-6.dll` | GCC libstdc++ | GPL-3.0-or-later WITH GCC-exception-3.1 |
| `libgomp-1.dll` | GCC OpenMP runtime | GPL-3.0-or-later WITH GCC-exception-3.1 |
| `libatomic-1.dll` | GCC atomic | GPL-3.0-or-later WITH GCC-exception-3.1 |
| `libquadmath-0.dll` | GCC quadmath | GPL-3.0-or-later WITH GCC-exception-3.1 |
| `libwinpthread-1.dll` | [winpthreads](https://github.com/meganz/mingw-std-threads) (MinGW-w64) | MIT |

## 许可证全文

### GPL-3.0 (GCC runtime libraries)

GCC runtime libraries (`libgcc_s_seh-1`, `libstdc++-6`, `libgomp-1`, `libatomic-1`, `libquadmath-0`) 采用 GPL-3.0-or-later，并附加 GCC Runtime Library Exception。GPL-3.0 全文见项目根目录的 [LICENSE](../../../LICENSE) 文件。

#### GCC Runtime Library Exception v3.1

```
GCC RUNTIME LIBRARY EXCEPTION

Version 3.1, 31 March 2009

Copyright (C) 2009 Free Software Foundation, Inc. <https://fsf.org/>

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.

This GCC Runtime Library Exception ("Exception") is an additional
permission under section 7 of the GNU General Public License, version
3 ("GPLv3"). It applies to a given file that was produced by the
compilation of a (the "GCC") of the GNU Compiler Collection.

When you use GCC to compile a program, GCC may incorporate portions
of its runtime libraries into the compiled program. This Exception
permits you to distribute such an executable, under certain
conditions, known as "linking exception." This is an exception to the
requirements of section 4 of GPLv3, which would otherwise require
you to distribute the Corresponding Source of the runtime library
along with the executable.

0. Definitions.

A file is an "Independent Module" if it either (1) is not a work
that was produced by the compilation of GCC; or (2) is a work that
was produced by the compilation of GCC, and all of its source code
is made available under the terms of this License and no additional
terms or conditions are imposed on the availability or use of the
source code.

"Eligible Compilable Code" for a version of the GCC means any
version of GCC, and any independent module that could be compiled by
that version of GCC, to produce an "Eligible Object Code."

"Eligible Object Code" for a version of the GCC means an object
code file that is produced by the compilation of Eligible Compilable
Code, and that does not include any additional code from GCC (other
than code that is part of a GCC runtime library).

1. Grant of Additional Permission.

You have permission to propagate a work of Eligible Object Code that
was compiled from Independent Modules, even though the object code
is combined with (incorporated into) a GCC runtime library, as long
as the object code is distributed under the terms of this Exception.

This Exception does not apply to code that you distribute separately
from the Eligible Object Code. This Exception does not grant any
other permission to propagate or distribute a GCC runtime library
or a work that is combined with or incorporates a GCC runtime
library.

2. Conditions.

A distribution of Eligible Object Code must carry prominent notices
stating that it was produced by compiling code with GCC, and must
include the text of this Exception, and a copy of the corresponding
Source Code of the Eligible Compilable Code, or a written offer to
provide the Source Code, as required by section 4 of GPLv3.

3. No Warranty.

The Eligible Object Code is distributed "AS IS," WITHOUT WARRANTY OF
ANY KIND, either express or implied, including, but not limited to,
the implied warranties of MERCHANTABILITY and FITNESS FOR A
PARTICULAR PURPOSE. See GPLv3 for more details.

4. Limitation of Liability.

In no event, unless required by applicable law or agreed to in
writing, will any copyright holder, or any other party who modifies
and/or conveys the Eligible Object Code, be liable to you for
damages, including any general, special, incidental or consequential
damages arising out of the use or inability to use the Eligible
Object Code, even if such holder or other party has been advised of
the possibility of such damages.
```

### MIT (winpthreads)

```
MIT License

Copyright (c) mingw-w64 project

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### GPL-2.0 (FFTW)

FFTW (`libfftw3-*.dll` 系列) 采用 GPL-2.0-or-later。GPL-2.0 全文见 <https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt>。

FFTW 的源代码可从 <http://www.fftw.org/download.html> 获取。

### LGPL-2.1 (Chromaprint)

libchromaprint 采用 LGPL-2.1-or-later。LGPL-2.1 全文见 <https://www.gnu.org/licenses/old-licenses/lgpl-2.1.txt>。

Chromaprint 的源代码可从 <https://acoustid.org/chromaprint> 获取。

## 兼容性说明

本项目主体采用 GPL-3.0-or-later 许可证。上述第三方组件的许可证与 GPL-3.0 兼容：

- GPL-2.0-or-later (FFTW) 可在 GPL-3.0 下使用
- LGPL-2.1-or-later (Chromaprint) 可在 GPL-3.0 下使用
- GPL-3.0-or-later WITH GCC-exception-3.1 (GCC runtime) 本身就是 GPL-3.0
- MIT (winpthreads) 与 GPL-3.0 兼容

Linux/macOS 平台依赖系统安装的 Chromaprint 库，不分发这些 DLL。
