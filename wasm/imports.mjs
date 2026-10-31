// Every import the module declares, that the host has no function for.
// A wasm import exists only when some reachable Aver code calls the
// operation, so a host function nobody has needed yet is invisible until the
// day a feature needs it -- and then it surfaces as "LinkError: Import #39
// requires a callable", naming an index. This names them all, before
// instantiation (n1bor/btc-listener#197; it bit twice). Its own module so a
// test can import it without starting the host.
export function missingImports(module, imports) {
  const missing = [];
  for (const { module: from, name, kind } of WebAssembly.Module.imports(module)) {
    if (kind !== "function") continue;
    if (typeof imports[from]?.[name] !== "function") missing.push(`${from}.${name}`);
  }
  return missing;
}

export function requireAllImports(module, imports) {
  const missing = missingImports(module, imports);
  if (missing.length > 0) {
    throw new Error(
      `the host implements none of these ${missing.length} import(s) the module declares: ${missing.join(", ")}`,
    );
  }
  return WebAssembly.Module.imports(module).filter((i) => i.kind === "function").length;
}
