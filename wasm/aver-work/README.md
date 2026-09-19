# Aver Work adapter

host.mjs, codec.mjs and worker.mjs are unmodified copies of tools/wasm-work
from the Aver commit pinned in ../../.aver-version (77ff2e66 at this update).
They implement aver:work/v1 using isolated Node worker threads. LICENSE is
copied from the same repository.

When moving the Aver pin, copy these three modules from that checkout.
The wasm CI job compares them with the pinned source before running tests.
Application socket policy and JSPI integration live in ../host.mjs.
