"""Patreon aliases for the shared provider models."""

from creeparr.providers.models import (
    CreatorInfo,
    MediaResource,
    MediaSpec,
    PostPage,
    PostResource,
    SubscriptionInfo,
    UserInfo,
)

CampaignInfo = CreatorInfo
PledgeInfo = SubscriptionInfo

__all__ = [
    "CampaignInfo",
    "CreatorInfo",
    "MediaResource",
    "MediaSpec",
    "PledgeInfo",
    "PostPage",
    "PostResource",
    "SubscriptionInfo",
    "UserInfo",
]
