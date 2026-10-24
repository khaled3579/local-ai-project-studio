# Native runtime correction included in the local installation

The source update alone does not replace the native runtime. Local 4.9.6 rebuilt `llama-common.dll` from llama.cpp commit `27c54b4bbcefadedcec6397477cc2e866c1db716` using MSVC 19.44 and the [Jinja JSON patch](../patches/llama-jinja-json-4.9.6.patch). It replaces the observed `std::ostringstream` construction path in `value_to_json` with a string sink and `std::to_chars`.

For a matching native build, apply that patch at the specified upstream commit and build `llama-common` with the compatible Windows x64 toolchain and dependencies. Preserve the engine ABI and existing CUDA/runtime components; do not mix arbitrary runtime builds. The locally installed DLL SHA256 is `f89f0b23cbaa37a57f0faf06724f8acc541ae0bd8724f40a121881ee18a06750`.

This publication includes the patch, not the DLL, PDB, crash dumps, debugger logs or a runtime ZIP. Source archives are not standalone runnable packages. The original memory-corruption source was not established; bypassing the observed crash path does not prove all access violations fixed. Existing build/export-symbol receipts were reviewed previously; no new tests or model generations were run for this publication.
