# nostr-proxy-py
Proxy between nostr clients and relays

Why a proxy is even needed?
- Hide your identity behind the proxy ip from public relays
- Public / private relays separation
- Connect multiple clients (possibly different keys) to the same proxy with a common list of public and private relays
- Cellular data plan friendly - the proxy filters duplicates from different relays instead of the client (soon) thus reduces traffic
- Apply custom filters (soon)

Usage example:

python nostr_proxy.py --listen-ip 0.0.0.0 --listen-port 5555 --private-servers wss://nostr.example.org:443 --public-servers wss://relay.damus.io:443

How to only send notes to private relays?
Add "[private]" anywhere in the content of the note and send to the proxy, can be any note type. 
