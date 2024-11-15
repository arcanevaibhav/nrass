import asyncio
import random
import ssl
import json
import time
import uuid
from loguru import logger
from websockets_proxy import Proxy, proxy_connect

# Constants
MAX_RETRIES = 5
MAX_CONNECTIONS = 100  
PING_INTERVAL = 30    
RETRY_DELAY = 10      
CHECK_INTERVAL = 60    

# Track proxy performance and status
proxy_health = {}
active_proxies = {}

# List of common User-Agent strings
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; WOW64; rv:49.0) Gecko/20100101 Firefox/49.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.159 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.111 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; WOW64; rv:44.0) Gecko/20100101 Firefox/44.0",
    "Mozilla/5.0 (Windows NT 6.1; WOW64; rv:52.0) Gecko/20100101 Firefox/52.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:83.0) Gecko/20100101 Firefox/83.0"
]

def get_random_user_agent():
    """Return a random User-Agent from the list"""
    return random.choice(USER_AGENTS)

async def connect_to_wss(proxy_url, user_id):
    device_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, proxy_url))
    retry_count = 0
    successful_attempts = proxy_health.get(proxy_url, {}).get('success', 0)
    failure_attempts = proxy_health.get(proxy_url, {}).get('fail', 0)
    
    logger.info(f"Initiating connection to {proxy_url} with Device ID: {device_id}")

    while retry_count < MAX_RETRIES:
        try:
            await asyncio.sleep(random.uniform(0.5, 1.5))  # Random delay to avoid overload
            # Select a random User-Agent
            custom_headers = {
                "User-Agent": get_random_user_agent()
            }
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            uri = "wss://proxy2.wynd.network:4444/"
            proxy = Proxy.from_url(proxy_url)

            async with proxy_connect(uri, proxy=proxy, ssl=ssl_context, extra_headers={
                "Origin": "chrome-extension://lkbnfiajjmbhnfledhphioinpickokdi",
                "User-Agent": custom_headers["User-Agent"]
            }) as websocket:
                
                logger.success(f"Successfully connected to {proxy_url}")
                retry_count = 0
                successful_attempts += 1
                proxy_health[proxy_url] = {'success': successful_attempts, 'fail': failure_attempts}
                active_proxies[proxy_url] = asyncio.current_task()

                async def send_ping():
                    while True:
                        try:
                            await websocket.send(json.dumps({
                                "id": str(uuid.uuid4()), "version": "1.0.0", "action": "PING", "data": {}
                            }))
                            logger.debug(f"Sent PING to {proxy_url}")
                            await asyncio.sleep(PING_INTERVAL)
                        except Exception as e:
                            logger.warning(f"Ping failed for {proxy_url}: {e}")
                            break  # Reconnect if ping fails

                send_ping_task = asyncio.create_task(send_ping())
                
                try:
                    while True:
                        response = await asyncio.wait_for(websocket.recv(), timeout=15)
                        message = json.loads(response)
                        logger.info(f"Received message from {proxy_url}: {message}")

                        # Handle AUTH action
                        if message.get("action") == "AUTH":
                            auth_response = {
                                "id": message["id"],
                                "origin_action": "AUTH",
                                "result": {
                                    "browser_id": device_id,
                                    "user_id": user_id,
                                    "user_agent": custom_headers['User-Agent'],
                                    "timestamp": int(time.time()),
                                    "device_type": "extension",
                                    "version": "4.26.2",
                                    "extension_id": "lkbnfiajjmbhnfledhphioinpickokdi"
                                }
                            }
                            await websocket.send(json.dumps(auth_response))
                            logger.info(f"Sent AUTH response to {proxy_url}")

                        # Handle PONG action
                        elif message.get("action") == "PONG":
                            pong_response = {"id": message["id"], "origin_action": "PONG"}
                            await websocket.send(json.dumps(pong_response))
                            logger.debug(f"Sent PONG response to {proxy_url}")

                        # Handle ERROR action
                        if message.get("action") == "ERROR":
                            error_message = message.get("message", "")
                            logger.error(f"Received ERROR from {proxy_url}: {error_message}")
                            proxy_health[proxy_url] = {'success': successful_attempts, 'fail': failure_attempts + 1}
                            active_proxies.pop(proxy_url, None)
                            return None
                except Exception as e:
                    logger.error(f"Exception occurred: {e}")
                finally:
                    send_ping_task.cancel()
                    logger.debug(f"Cancelled ping task for {proxy_url}")

        except (Exception, asyncio.TimeoutError) as e:
            retry_count += 1
            failure_attempts += 1
            proxy_health[proxy_url] = {'success': successful_attempts, 'fail': failure_attempts}
            logger.error(f"Connection attempt {retry_count}/{MAX_RETRIES} failed for {proxy_url}: {str(e)}")

            if retry_count >= MAX_RETRIES:
                active_proxies.pop(proxy_url, None)
                logger.error(f"Exceeded maximum retries for {proxy_url}, removing from active proxies.")
                return None

            await asyncio.sleep(RETRY_DELAY)

async def monitor_connections():
    while True:
        logger.debug("Monitoring active proxy connections.")
        await asyncio.sleep(CHECK_INTERVAL)

async def main():
    _user_id = input("Enter your User ID: ").strip()
    proxy_file = 'proxy.txt'
    with open(proxy_file, 'r') as file:
        all_proxies = file.read().splitlines()

    for _ in range(min(MAX_CONNECTIONS, len(all_proxies))):
        proxy_url = get_fallback_proxy(all_proxies)
        active_proxies[proxy_url] = asyncio.create_task(connect_to_wss(proxy_url, _user_id))

    asyncio.create_task(monitor_connections())

    while True:
        if active_proxies:  
            done, pending = await asyncio.wait(active_proxies.values(), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                failed_proxy = next((proxy for proxy, t in active_proxies.items() if t == task), None)
                if failed_proxy:
                    active_proxies.pop(failed_proxy)
                    logger.info(f"Retrying connection for failed proxy: {failed_proxy}")
                    new_proxy = get_fallback_proxy(all_proxies)
                    active_proxies[new_proxy] = asyncio.create_task(connect_to_wss(new_proxy, _user_id))

        await asyncio.sleep(CHECK_INTERVAL)
        
        for proxy in set(all_proxies) - active_proxies.keys():
            if len(active_proxies) < MAX_CONNECTIONS:
                active_proxies[proxy] = asyncio.create_task(connect_to_wss(proxy, _user_id))

def get_fallback_proxy(all_proxies):
    return random.choice(all_proxies)

if __name__ == '__main__':
    try:
        logger.info("Starting proxy connection manager...")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Graceful shutdown initiated.")
