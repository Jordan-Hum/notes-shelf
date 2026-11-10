# Structured Notes Shelf

A password-protected dashboard of current structured note offerings from CIBC, National Bank, BMO and Scotiabank.

The page content is encrypted (AES-256-GCM, key derived from the password with PBKDF2-SHA256, 600,000 iterations). This repository only holds the encrypted page and the code that builds it; note data is never committed in readable form.

## Refreshing

The **Refresh data** button on the site runs the `Refresh notes` workflow, which reads each bank's current offerings, rebuilds the page, encrypts it and publishes it (about 4–5 minutes). It can also be run from the Actions tab.

Repository secrets used by the workflow:

- `NOTES_PASSWORD` – the site password (used to encrypt the page)
- `REFRESH_TOKEN` – a fine-grained token limited to this repository with **Actions: Read and write**; it is stored only inside the encrypted page and lets the button start the workflow

If one bank can't be reached, the previous data for that bank is kept and the page says so.
