---
doc_id: troubleshooting-login
title: Troubleshooting Sign-in and SSO Problems
category: troubleshooting
tiers: [all]
---
# Troubleshooting Sign-in and SSO Problems

## "Incorrect email or password"

Confirm the email address is the one on the account and check whether Caps Lock is on. After 5 failed attempts the account is locked for 15 minutes. Use "Forgot password" to reset it.

## SSO sign-in fails

SSO is available on Pro (Google Workspace) and Enterprise (any SAML 2.0 identity provider).

- An error that says "User not provisioned" means the user has not been assigned the Nimbus app in the identity provider. An administrator must assign them.
- An error that says "Invalid SAML response" usually means the identity provider certificate has expired. An administrator must upload the new certificate in Admin Console > Security > SSO.
- If SSO is broken for everyone in an Enterprise organisation, administrators can use the emergency break-glass owner account, which signs in with a password and 2FA.

## Account locked or disabled

An account disabled by an organisation administrator can only be re-enabled by that administrator. Support cannot override administrator decisions.
