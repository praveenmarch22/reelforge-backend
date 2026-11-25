"""Outside services (AI models, stock libraries). Taken from the prototype in pipeline/.

Each kind of work has an ordered list of providers. A call tries each one in turn (each with
retries), skipping any that has no key or that the admin has switched off (G-1, G-3).
"""
