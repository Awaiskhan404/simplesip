"""
Simple SIP Client Library (simplesip)

A Python library for SIP (Session Initiation Protocol) communication with RTP audio streaming capabilities.
"""

__version__ = "0.1.5"
__author__ = "Awais Khan"
__email__ = "contact@awaiskhan.com.pk"

from .client import SimpleSIPClient, CallState

__all__ = ["SimpleSIPClient", "CallState"]