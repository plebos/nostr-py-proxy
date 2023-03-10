import asyncio
import websockets
import logging
import argparse
import random
from urllib.parse import urlparse
from pythonostr.nostr.event import Event
from pythonostr.nostr.message_type import RelayMessageType
import time
import json
import sys
import re
import requests


#TODO: avoid globals

messages_cache = {}
duplicates_count = 0 
large_media_files_count = 0

connected_clients = set()
connected_public_servers = set()
connected_private_servers = set()

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)


def parse_args():

    parser = argparse.ArgumentParser()
    parser.add_argument("--public-servers", nargs='+', default=["wss://relay.damus.io:443", "wss://nos.lol:443"], help="List of public servers in the format <url> <port>")
    parser.add_argument("--private-servers", nargs='+', default=[], help="List of private servers in the format <url> <port>")
    parser.add_argument("--listen-ip", default="127.0.0.1", help="IP address to listen on")
    parser.add_argument("--listen-port", default=9999, type=int, help="Port to listen on")
    parser.add_argument("--note-cache-time", default=120, type=int, help="Seconds till a note is deleted from cache to check for duplicates")
    parser.add_argument("--filter-large-media", action='store_false', help="Filter notes with links to large media files")
    
    return parser.parse_args()

def argmax(arr):
    max_val = float('-inf')
    max_idx = None
    for i, val in enumerate(arr):
        if val > max_val:
            max_val = val
            max_idx = i
    return max_idx

def extract_image_urls(text):
    # Regular expression to match image URLs
    pattern = r'\b(?:https?://\S+(?:jpg|png|gif))\b'
    # Match all URLs in the text
    urls = re.findall(pattern, text)
    # Filter out non-image URLs
    image_urls = [url for url in urls if url.endswith(('.jpg', '.png', '.gif'))]
    return image_urls

def get_file_size(url):
    response = requests.head(url)
    if 'Content-Length' in response.headers:
        file_size = int(response.headers['Content-Length'])
    else:
        file_size = None
    return file_size

async def handle_client(websocket):
    global connected_clients, connected_public_servers, connected_private_servers
    #logger.debug(f"HEEELLLLOO")
    connected_clients.add(websocket)
    try:
        while True:
            message = await websocket.recv()
            logger.info(f"Received {message}")
            # send the message to all connected private servers
            for server in connected_private_servers:
                try:
                    await server.send(message)
                except websockets.exceptions.ConnectionClosed:
                    connected_private_servers.remove(server)
                    await retry_server_connection(server, True)
            logger.info(f"Sent message to private servers: {message}")
            
            # check if message contains "[private]" then don't post it to public relays

            if "[private]" in message:
                continue
            
            # send the message to all connected public servers
            for server in connected_public_servers:
                try:
                    await server.send(message)
                except websockets.exceptions.ConnectionClosed:
                    connected_public_servers.remove(server)
                    await retry_server_connection(server, False)
            logger.info(f"Sent message to public servers: {message}")
    except websockets.exceptions.ConnectionClosed:
        connected_clients.remove(websocket)
        logger.warning(f"Client disconnected")
    except Exception as e:
        logger.error(f"Error in handle_client: {e}")

async def handle_server(websocket, filter_large_media):
    global messages_cache, duplicates_count, connected_private_servers, connected_public_servers, large_media_files_count

    if websocket in connected_public_servers:
        connected_servers = connected_public_servers
    else:
        connected_servers = connected_private_servers
    try:
        while True:
            message = await websocket.recv()
            message = message.strip("\n")
            if not message or message[0] != "[" or message[-1] != "]":
                continue

            message_json = json.loads(message)
            message_type = message_json[0]
            if not RelayMessageType.is_valid(message_type):
                continue
        
            if message_type == RelayMessageType.EVENT:
                if not len(message_json) == 3:
                    continue
        
                e = message_json[2]
                event = Event(
                    e["content"],
                    e["pubkey"],
                    e["created_at"],
                    e["kind"],
                    e["tags"],
                    e["sig"],
                )

                if not event.verify():
                    continue


                if e["sig"] in messages_cache:
                    duplicates_count += 1
                    continue
                else:
                    messages_cache[e["sig"]] = time.time()

                if filter_large_media:
                    urls = extract_image_urls(e["content"])
                    if urls:
                        file_sizes = [get_file_size(url) for url in urls]
                        max_file_size = max(file_sizes)
                        max_file_size_index = argmax(file_sizes)
                        # TODO: make size configurable
                        if max_file_size > 1000000:
                            # file bigger than 1MB
                            large_media_files_count += 1
                            logger.info(f"Note {event.id} with a large ({max_file_size}b) media file ({urls[max_file_size_index]}) filtered!")
                            continue

            # send the message to all connected clients
            for client in connected_clients:
                await client.send(message)
            #logger.info(f"Sent message to clients: {message}")
    except websockets.exceptions.ConnectionClosed:
        connected_servers.remove(websocket)
        logger.warning(f"Server disconnected")
        await retry_server_connection(websocket, websocket in connected_private_servers)
    except Exception as e:
        logger.error(f"Error in handle_server: {e}")
        exc_type, exc_obj, exc_tb = sys.exc_info()
        logger.error(f"Exception type: {exc_type}, Exception object: {exc_obj}, Line number: {exc_tb.tb_lineno}")

async def cleanup_messages_cache(time_limit):
    global messages_cache, duplicates_count
    # Remove any messages whose timestamps are older than the time limit
    
    while True:
        logger.info(f"Refreshing notes cache (cache size: {len(messages_cache)}, duplicates count: {duplicates_count})")
        now = time.time()
        for sig, timestamp in list(messages_cache.items()):
            if now - timestamp >= time_limit:
                del messages_cache[sig]
                logger.info(f"Note with siganture {sig} leaves cache")
                
        await asyncio.sleep(5)


async def retry_server_connection(server, is_private):
    async def retry():
        try:
            await asyncio.sleep(random.uniform(1,5))
            new_server = await websockets.connect(f"{server[0]}:{server[1]}")
            if is_private:
                connected_private_servers.add(new_server)
                logger.info(f"Reconnected to private server {server[0]}:{server[1]}")
            else:
                connected_public_servers.add(new_server)
                logger.info(f"Reconnected to public server {server[0]}:{server[1]}")
        except Exception as e:
            logger.error(f"Error reconnecting to server {server[0]}:{server[1]}: {e}")
            retry()
    asyncio.create_task(retry())

async def connect_to_server(server, public=True, filter_large_media=False):
    while True:
        try:
            logger.info(f"Trying to establish a connection to {'public' if public else 'private'} server: {server[0]}:{server[1]}")
            s = await websockets.connect(f"{server[0]}:{server[1]}")
            if public:
                connected_public_servers.add(s)
            else:
                connected_private_servers.add(s)
            logger.info(f"Connected to server {server[0]}:{server[1]}")
            await handle_server(s, filter_large_media)
            
        except Exception as e:
            logger.error(f"Error connecting to server {server[0]}:{server[1]}, error: {e}, retryting..")
            await asyncio.sleep(5)
async def connect_to_servers(public_servers, private_servers, filter_large_media=False):
    logger.debug(f"Connecting to servers")
    try:
        tasks = []

        for server in public_servers:
            logger.debug(f"Appending {server}")
            tasks.append(asyncio.create_task(connect_to_server(server, True, filter_large_media)))

        for server in private_servers:
            logger.debug(f"Appending {server}")
            tasks.append(asyncio.create_task(connect_to_server(server, False, filter_large_media)))

        await asyncio.gather(*tasks)		
    except Exception as e:
        logger.error(f"Error connecting to servers: {e}")

async def websocket_server(func, ip, port):
    server = await websockets.serve(handle_client, ip, 10000)
    logger.info(f"[*] Starting a websocket server on {ip}:{port}")
    try:
        await server.wait_closed()
    except asyncio.CancelledError:
        server.close()
        await server.wait_closed()
    except Exception as exc:
        logger.exception(f"Error in websockets server: {exc}")
        server.close()
        await server.wait_closed()
    return


async def main():
    args = parse_args() 
    logger.info(f"[*] Starting nostr proxy")
    #url_port_tuples = [ in urls]
    public_servers = [(f"{urlparse(url).scheme}://{urlparse(url).hostname}", urlparse(url).port) for url in args.public_servers]
    private_servers = [(f"{urlparse(url).scheme}://{urlparse(url).hostname}", urlparse(url).port) for url in args.private_servers]

    logger.info(f"[*] Loaded {len(public_servers)} public servers")
    logger.info(f"[*] Loaded {len(private_servers)} private servers")
    # Start the client listening for incoming connections	

    logger.debug(f"{args.listen_ip} {args.listen_port}")

    #start_server = websockets.serve(handle_client, args.listen_ip, args.listen_port)
    #asyncio.ensure_future(start_server)

    #await websocket_server(handle_client, "127.0.0.1", 1231)
    #await websockets.serve(handle_client, "localhost", 3502)
    tasks = [
        asyncio.create_task(websocket_server(handle_client, args.listen_ip, args.listen_port)),
        asyncio.create_task(connect_to_servers(public_servers, private_servers, args.filter_large_media)),
        asyncio.create_task(cleanup_messages_cache(args.note_cache_time))
    ]

    await asyncio.gather(*tasks)

    #logger.info(f"Reconnected to private server")

if __name__ == "__main__":
    asyncio.run(main())