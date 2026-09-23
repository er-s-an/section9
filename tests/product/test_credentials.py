from s9.product.credentials import CredentialUnavailable, ProductCredentialBroker


def test_langfuse_reference_resolves_only_explicit_customer_source_prefix():
    broker = ProductCredentialBroker({
        "S9_OBSERVED_LANGFUSE_PUBLIC_KEY": "pk-test",
        "S9_OBSERVED_LANGFUSE_SECRET_KEY": "sk-test",
        "S9_OBSERVED_LANGFUSE_PROJECT_ID": "project-test",
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": "section9-telemetry-pk",
        "LANGFUSE_INIT_PROJECT_SECRET_KEY": "section9-telemetry-sk",
    })

    credentials = broker.langfuse("env://S9_OBSERVED_LANGFUSE")
    assert credentials.public_key == "pk-test"
    assert credentials.secret_key == "sk-test"
    assert credentials.project_id == "project-test"
    assert "sk-test" not in repr(credentials)


def test_missing_customer_credentials_never_fall_back_to_section9_telemetry():
    broker = ProductCredentialBroker({
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": "section9-telemetry-pk",
        "LANGFUSE_INIT_PROJECT_SECRET_KEY": "section9-telemetry-sk",
    })
    try:
        broker.langfuse("env://S9_OBSERVED_LANGFUSE")
    except CredentialUnavailable as exc:
        assert exc.code == "CREDENTIAL_NOT_CONFIGURED"
    else:
        raise AssertionError("customer data connector must not reuse Section9 telemetry credentials")


def test_unsupported_reference_and_provider_token_are_not_exposed():
    broker = ProductCredentialBroker({"S9_OBSERVED_GITHUB_TOKEN": "ghp-sensitive"})
    github = broker.github("env://S9_OBSERVED_GITHUB")
    assert "ghp-sensitive" not in repr(github)
    try:
        broker.github("literal-token-value")
    except CredentialUnavailable as exc:
        assert exc.code == "CREDENTIAL_REFERENCE_UNSUPPORTED"
        assert "ghp-sensitive" not in str(exc)
    else:
        raise AssertionError("arbitrary credential input must be rejected")
