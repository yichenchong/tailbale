"""Integration tests: full router->service->reconciler->edge/DNS chain.

Unlike the rest of the suite (whose autouse conftest fixtures globally stub
Docker, upstream validation, and background reconcile), the tests in this
package opt OUT of those mocks and drive the reconcile pipeline end-to-end
against an injected fake-but-realistic Docker client + tmp cert/generated dirs.
"""
