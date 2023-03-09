import asyncio
import websockets
import logging
import argparse
import random
from urllib.parse import urlparse
from pythonostr.nostr import event
from pythonostr.nostr.message_type import RelayMessageType
import time
import json

# Init messages cache   
messages_cache = {}

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

def parse_args():

    parser = argparse.ArgumentParser()
    parser.add_argument("--public-servers", nargs='+', default=[("server1", 8765), ("server2", 8765), ("server3", 8765)], help="List of public servers in the format <url> <port>")
    parser.add_argument("--private-servers", nargs='+', default=[("private_server1", 8765), ("private_server2", 8765), ("private_server3", 8765)], help="List of private servers in the format <url> <port>")
    parser.add_argument("--listen-ip", default="localhost", help="IP address to listen on")
    parser.add_argument("--listen-port", default=8765, type=int, help="Port to listen on")
    parser.add_argument("--note-cache-time", default=120, type=int, help="Seconds till a note is deleted from cache to check for duplicates")
    parser.add_argument("--filter-large-media", action='store_true', help="Filter notes with links to large media files")
    
    return parser.parse_args()

async def handle_client(websocket):
    connected_clients.add(websocket)
    try:
        while True:
            message = await websocket.recv()
            # send the message to all connected private servers
            for server in connected_private_servers:
                try:
                    await server.send(message)
                except websockets.exceptions.ConnectionClosed:
                    connected_private_servers.remove(server)
                    retry_server_connection(server, True)
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
                    retry_server_connection(server, False)
            logger.info(f"Sent message to public servers: {message}")
    except websockets.exceptions.ConnectionClosed:
        connected_clients.remove(websocket)
        logger.warning(f"Client disconnected")
    except Exception as e:
        logger.error(f"Error in handle_client: {e}")

async def handle_server(websocket, path):
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
        
            if message_type == RelayMessageType.EVENT and not len(message_json) == 3:
                continue

            e = message_json[2]
            event = event.Event(
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
                continue
            else:
                messages_cache[e["sig"]] = time.time()

            # send the message to all connected clients
            for client in connected_clients:
                await client.send(message)
            logger.info(f"Sent message to clients: {message}")
    except websockets.exceptions.ConnectionClosed:
        connected_servers.remove(websocket)
        logger.warning(f"Server disconnected")
        retry_server_connection(websocket, websocket in connected_private_servers)
    except Exception as e:
        logger.error(f"Error in handle_server: {e}")

def cleanup_messages_cache(time_limit):
    # Remove any messages whose timestamps are older than the time limit
    while True:
        now = time.time()
        for sig, timestamp in list(messages_cache.items()):
            if now - timestamp >= time_limit:
                del messages_cache[sig]
        time.sleep(5)


def retry_server_connection(server, is_private):
    async def retry():
        try:
            await asyncio.sleep(random.uniform(1,5))
            new_server = await websockets.connect(f"ws://{server[0]}:{server[1]}")
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

async def connect_to_servers():
    try:
        tasks = []	
        for server in public_servers:
            s = await websockets.connect(f"{server[0]}:{server[1]}")
            connected_public_servers.add(s)
            task = asyncio.create_task(handle_server(s, None))
            tasks.append(task)
            logger.info(f"Connected to public server {server[0]}:{server[1]}")
        for server in private_servers:
            s = await websockets.connect(f"{server[0]}:{server[1]}")
            connected_private_servers.add(s)
            task = asyncio.create_task(handle_server(s, None))
            tasks.append(task)
            logger.info(f"Connected to private server {server[0]}:{server[1]}")
        await asyncio.gather(*tasks)		
    except Exception as e:
        logger.error(f"Error connecting to servers: {e}")

        

if __name__ == "__main__":
    args = parse_args() 
    print(args.public_servers)
    print([(server[0], server[1]) for server in args.public_servers])
    
    #url_port_tuples = [ in urls]
    public_servers = [(f"{urlparse(url).scheme}://{urlparse(url).hostname}", urlparse(url).port) for url in args.public_servers]
    print(public_servers)
    private_servers = [(f"{urlparse(url).scheme}://{urlparse(url).hostname}", urlparse(url).port) for url in args.private_servers]
    connected_clients = set()
    connected_public_servers = set()
    connected_private_servers = set()
    # Start the client listening for incoming connections	
    start_server = websockets.serve(handle_client, args.listen_ip, args.listen_port)
    asyncio.ensure_future(start_server)
    asyncio.ensure_future(connect_to_servers())
    asyncio.ensure_future(cleanup_messages_cache(args.note_cache_time))
    try:
        asyncio.get_event_loop().run_forever()
    except Exception as e:
        logger.error(f"Error in main: {e}")