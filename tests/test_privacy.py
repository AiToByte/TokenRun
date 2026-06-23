"""Tests for gateway.privacy — reversible PII masking."""

import pytest

from gateway.privacy import PrivacyRedactor


class TestPrivacyRedactor:
    def test_mask_email(self):
        r = PrivacyRedactor()
        masked = r.mask("Contact: alice@example.com")
        assert "alice@example.com" not in masked
        assert "[[TR_EMAIL_1]]" in masked

    def test_mask_phone(self):
        r = PrivacyRedactor()
        masked = r.mask("Phone: 13812345678")
        assert "13812345678" not in masked
        assert "[[TR_PHONE_1]]" in masked

    def test_mask_id_card(self):
        r = PrivacyRedactor()
        # Use an ID card where no substring matches the phone pattern (1[3-9]...)
        # 000... ensures no phone-pattern match since leading digit is 0
        masked = r.mask("ID: 000000200001010029")
        assert "000000200001010029" not in masked
        assert "[[TR_ID_CARD_1]]" in masked

    def test_mask_ip_address(self):
        r = PrivacyRedactor()
        masked = r.mask("Server: 192.168.1.100")
        assert "192.168.1.100" not in masked
        assert "[[TR_IP_ADDR_1]]" in masked

    def test_unmask_restores_original(self):
        r = PrivacyRedactor()
        original = "Email alice@test.com or call 13800138000"
        masked = r.mask(original)
        restored = r.unmask(masked)
        assert restored == original

    def test_unmask_preserves_non_sensitive(self):
        r = PrivacyRedactor()
        original = "Hello world, no PII here."
        masked = r.mask(original)
        assert masked == original
        restored = r.unmask(masked)
        assert restored == original

    def test_same_value_same_placeholder(self):
        r = PrivacyRedactor()
        text = "a@test.com and a@test.com"
        masked = r.mask(text)
        # Both occurrences should use the same placeholder
        assert masked.count("[[TR_EMAIL_1]]") == 2

    def test_different_values_different_placeholders(self):
        r = PrivacyRedactor()
        text = "a@test.com and b@test.com"
        masked = r.mask(text)
        assert "[[TR_EMAIL_1]]" in masked
        assert "[[TR_EMAIL_2]]" in masked

    def test_clear_vault(self):
        r = PrivacyRedactor()
        r.mask("alice@test.com")
        assert r.vault_size == 1
        r.clear_vault()
        assert r.vault_size == 0

    def test_vault_size(self):
        r = PrivacyRedactor()
        r.mask("a@test.com b@test.com c@test.com")
        assert r.vault_size == 3

    def test_custom_rules_filter(self):
        """With rules=['emails'], only emails should be masked."""
        r = PrivacyRedactor(rules=["EMAIL"])
        masked = r.mask("Email: a@test.com Phone: 13800138000")
        assert "[[TR_EMAIL_1]]" in masked
        assert "13800138000" in masked  # phone NOT masked

    def test_mask_multiple_categories(self):
        r = PrivacyRedactor()
        text = "Contact alice@test.com or 13800138000, server 10.0.0.1"
        masked = r.mask(text)
        assert "alice@test.com" not in masked
        assert "13800138000" not in masked
        assert "10.0.0.1" not in masked

    def test_unmask_after_multiple_masks(self):
        r = PrivacyRedactor()
        texts = [
            "alice@test.com",
            "bob@test.com 13900139000",
            "192.168.0.1",
        ]
        for original in texts:
            masked = r.mask(original)
            restored = r.unmask(masked)
            assert restored == original

    def test_empty_string(self):
        r = PrivacyRedactor()
        assert r.mask("") == ""
        assert r.unmask("") == ""

    def test_mask_api_key(self):
        r = PrivacyRedactor()
        masked = r.mask("Key: sk-abcdefghijklmnopqrstuvwx")
        assert "sk-abcdefghijklmnopqrstuvwx" not in masked
        assert "[[TR_API_KEY_1]]" in masked

    def test_rules_normalisation_lowercase(self):
        """Rules from SecurityConfig use lowercase plural — should still match."""
        r = PrivacyRedactor(rules=["emails", "api_keys"])
        masked = r.mask("Email: a@test.com Key: sk-abcdefghijklmnopqrstuvwx")
        assert "[[TR_EMAIL_" in masked
        assert "[[TR_API_KEY_" in masked

    def test_rules_normalisation_uppercase(self):
        """Rules already in uppercase should work too."""
        r = PrivacyRedactor(rules=["EMAIL", "API_KEY"])
        masked = r.mask("Email: a@test.com Key: sk-abcdefghijklmnopqrstuvwx")
        assert "[[TR_EMAIL_" in masked
        assert "[[TR_API_KEY_" in masked
