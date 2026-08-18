"""HTTP helpers."""
import aiohttp

from udemy_enroller.logger import get_logger

logger = get_logger()


async def http_get(url, headers=None):
    """
    Send REST get request to the url passed in.

    :param url: The Url to get call get request on
    :param headers: The headers to pass with the get request
    :return: data if any exists
    """
    if headers is None:
        headers = {}
    try:
        resolver = aiohttp.ThreadedResolver()
        connector = aiohttp.TCPConnector(resolver=resolver)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                text = await response.read()
                return text
    except Exception as e:
        logger.error(f"Error in get request: {e}")
        return b""
