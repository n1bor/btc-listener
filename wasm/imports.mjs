// Every import the module declares that neither the host nor the Work
// adapter has a function for.
//
// A wasm import exists only when some reachable Aver code calls the
// operation, so a host function nobody has needed yet is invisible until the
// day a feature needs it. It used to surface as "LinkError: Import #39
// requires a callable", naming an index; since the Work adapter came in it
// does not surface at all: the adapter resolves every declared import it is
// not handed to a stub that throws `host: missing owner.name` when first
// called, which can be an hour into a run. This names them all up front
// (n1bor/btc-listener#197; it bit twice before the stubs, and the stubs make
// a third time quieter, not rarer). Its own module so a test can import it
// without starting the host.
import { createWorkHost } from "./aver-work/host.mjs";

// A host value counts as providing an import when wasm can call it: a
// function, or a JSPI `WebAssembly.Suspending` wrapping one -- which is what
// every import that awaits a socket is, and whose typeof is "object".
export function provides(value) {
  if (typeof value === "function") return true;
  const Suspending = globalThis.WebAssembly?.Suspending;
  return typeof Suspending === "function" && value instanceof Suspending;
}

// What the Work adapter provides on its own, read from the adapter itself:
// run it on a module that declares nothing and keep what it put in the
// table. No list kept here can drift from the adapter CI pins byte for byte.
const EMPTY_MODULE = new Uint8Array([0x00, 0x61, 0x73, 0x6d, 1, 0, 0, 0]);
export async function adapterProvides() {
  const host = await createWorkHost(new WebAssembly.Module(EMPTY_MODULE), { imports: {} });
  try {
    return host.imports;
  } finally {
    await host.close();
  }
}

export function missingImports(module, hostImports, adapterImports = {}) {
  const missing = [];
  for (const { module: from, name, kind } of WebAssembly.Module.imports(module)) {
    if (kind !== "function") continue;
    if (provides(hostImports[from]?.[name])) continue;
    if (provides(adapterImports[from]?.[name])) continue;
    missing.push(`${from}.${name}`);
  }
  return missing;
}

// Throws, naming every missing import, or returns how many function imports
// the module declares.
export async function requireAllImports(module, hostImports) {
  const missing = missingImports(module, hostImports, await adapterProvides());
  if (missing.length > 0) {
    throw new Error(
      `neither the host nor the Work adapter implements these ${missing.length} import(s) the module declares: ${missing.join(", ")}`,
    );
  }
  return WebAssembly.Module.imports(module).filter((i) => i.kind === "function").length;
}
