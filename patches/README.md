# Project-specific gem5 patches

`gem5-kvm-configure-context.patch` records a local gem5 build workaround for
`src/cpu/kvm/SConsopts`. It is separate from the RAPAF simulation extension and
is not applied automatically. Apply it from the gem5 checkout only if that
configuration issue occurs:

```sh
git apply --unidiff-zero /path/to/RAPAF/patches/gem5-kvm-configure-context.patch
```

The external gem5 checkout was restored after capturing this patch.
