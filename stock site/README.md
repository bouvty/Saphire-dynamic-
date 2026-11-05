# Saphire Stock Website

Static HTML/CSS/JavaScript website served by a separate Vercel project named `saphire-stock`.

- Vercel Git repository: `bouvty/Saphire-dynamic-` (`main` branch)
- Project Root Directory: `stock site`
- Custom domain: `stock.saphire.rip`
- Live inventory: `https://api.saphire.rip/v2/stock/members/` (refreshes every 60 seconds)
- Guild checker: `https://api.saphire.rip/v2/stock/members/check/guild?guild_id=...`
- Pretty results URL: `/stock/check/guild?guild_id=...`, rewritten to `index.html` in `vercel.json`.

## Cloudflare DNS

The domain is managed by Cloudflare. Vercel may report the project domain as verified before DNS routing is configured. Create a DNS-only CNAME record:

- Type: `CNAME`
- Name: `stock`
- Target: `c87587245884a94b.vercel-dns-017.com`
- Proxy status: `DNS only`
- TTL: `Auto`

After propagation, confirm `stock.saphire.rip` loads and the guild details route reloads successfully. This project contains no API keys or bot credentials. The existing Railway backend remains separate.
