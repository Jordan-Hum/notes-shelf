# Structured Notes Shelf

A password-protected dashboard of current structured note offerings from CIBC, National Bank, BMO and Scotiabank.

The page content is encrypted (AES-256-GCM, key derived from the password with PBKDF2-SHA256, 600,000 iterations). This repository only holds the encrypted page; the data is decrypted in the browser after the correct password is entered.
