# Security Policy

## Supported versions

Only the latest release of GrooveShop Django API receives security fixes. A
fix is made on `main` and ships in the next release, which the platform deploys
by pinning its image tag in `grooveshop-infrastructure`. There are no
maintenance branches, so earlier releases get no backports.

## Reporting a vulnerability

Report a vulnerability privately through GitHub: open the repository's
**Security** tab and choose **Report a vulnerability**. Do not open a public
issue or pull request for it, and do not post exploit details publicly before a
fix is released.

Include the affected endpoint or module, the steps to reproduce it and what an
attacker gains. Never include real customer data or live credentials.

You will get an acknowledgement and, once the report is assessed, whether it is
accepted or declined and why. An accepted report is fixed in a new release and
the reporter is told when it ships.
