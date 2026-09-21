"""Video module (§33–§34).

OWNS: video_assets, video_sessions. Issues short-lived playback sessions only
after independent entitlement verification; provider credentials never reach
the client.
"""

TABLES_OWNED = ("video_assets", "video_sessions")
