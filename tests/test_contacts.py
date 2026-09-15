from marketingiq.application.contacts import match_buyer_role
from marketingiq.domain.models import BuyerRoleMatch
from marketingiq.domain.providers import ProviderCapability
from marketingiq.infrastructure.providers import HunterProvider


def test_buyer_role_matching_is_deterministic():
    assert match_buyer_role("VP Marketing", "VP Marketing", None, None)[0] == BuyerRoleMatch.EXACT
    assert (
        match_buyer_role("VP Marketing", "Marketing Vice President", None, None)[0]
        == BuyerRoleMatch.PARTIAL
    )
    assert (
        match_buyer_role("VP Marketing", "Engineer", "Engineering", None)[0]
        == BuyerRoleMatch.UNKNOWN
    )


def test_hunter_advertises_contact_capabilities_without_calling_live_api():
    provider = HunterProvider(api_key=None)
    assert ProviderCapability.SEARCH_CONTACTS in provider.capabilities
    assert ProviderCapability.FIND_EMAIL in provider.capabilities
    assert ProviderCapability.VERIFY_EMAIL in provider.capabilities
    assert not provider.configured
