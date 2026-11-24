---
doc_id: troubleshooting-sync
title: Troubleshooting File Sync Problems
category: troubleshooting
tiers: [all]
---
# Troubleshooting File Sync Problems

## Files are not syncing

1. Check the Nimbus desktop app status icon. A grey cloud means sync is paused; click it and choose "Resume sync".
2. Confirm the device is online and that a firewall or VPN is not blocking `sync.nimbuscloud.example` on port 443.
3. Make sure the account has free storage. When storage is full, uploads stop but downloads continue.
4. Restart the desktop app. On Windows, right-click the tray icon and choose Quit, then reopen it.
5. If a single file fails, check the file name. Names longer than 255 characters or containing the characters `< > : " | ? *` cannot sync.

## Sync conflicts

When the same file is edited on two devices while offline, Nimbus keeps both versions and renames one to `filename (conflicted copy).ext`. Compare the files, keep the correct one and delete the other.

## Slow sync

Large initial uploads can take several hours. Upload speed can be limited in Settings > Network; set it to "Don't limit" for faster sync. Pro and Enterprise customers can enable LAN sync to copy files directly between devices on the same network.

## When to escalate

If sync has been stuck for more than 24 hours after following these steps, or files are missing from all devices, create an escalation ticket with category `technical`. Missing files may be recoverable from version history (30 days on Free, 90 days on Starter, 180 days on Pro, 1 year on Enterprise).
