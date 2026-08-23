# toolburn.com Site

This wrapper serves the public Toolburn documentation site.

Rules:

- keep the public page rendered from `/srv/dark/repos/toolburn/README.md`
- keep domain-specific config outside `public/`
- do not add a build step; README changes should show on the next request
- keep the surface static-looking, fast, and public-safe
- real product truth belongs in the Toolburn repo, not duplicated here
