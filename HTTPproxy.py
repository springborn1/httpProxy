import signal
from optparse import OptionParser
from socket import *
from urllib.parse import urlparse
import sys
from _thread import *

# Enable/disable caching and domain blocking
caching = False
blocking = False
cache = {}
blocklist = set()

# Signal handler for pressing ctrl-c
def ctrl_c_pressed(signal, frame):
	sys.exit(0)

# Checks for valid HTTP message format from client
# Param message: HTTP message from client
# Return: Either HTTP error number or url
def checkMessage(message):
    lines = message.splitlines()
    request = lines[0].split(" ")
    if len(request) != 3:
        return 400
    if request[0] != "GET":
        return 501
    # Validate url
    url = urlparse(request[1])
    if not url.hostname or url.path == "":
        return 400
    if request[2] != "HTTP/1.0":
        return 400
    if checkPath(url.path):
        return 200
    global blocking
    global blocklist
    if blocking:
        for s in blocklist:
            if s in url.hostname or url.hostname in s:
                return 403
    h = checkHeaders(lines)
    if h:
        # Return url
        return request[1]
    return 400

# Checks the url path for cache or blocklist control commands
# Param path: Absolute url from client request
# Return: True if url is control command, otherwise False
def checkPath(path):
    global caching
    global cache
    global blocking
    global blocklist
    if path == "/proxy/cache/enable":
        caching = True
    elif path == "/proxy/cache/disable":
        caching = False
    elif path == "/proxy/cache/flush":
        cache = {}
    elif path == "/proxy/blocklist/enable":
        blocking = True
    elif path == "/proxy/blocklist/disable":
        blocking = False
    elif path == "/proxy/blocklist/flush":
        blocklist = set()
    elif path.startswith("/proxy/blocklist/add/"):
        path = path.replace("/proxy/blocklist/add/", "")
        blocklist.add(path)
    elif path.startswith("/proxy/blocklist/remove/"):
        path = path.replace("/proxy/blocklist/remove/", "")
        blocklist.remove(path)
    else:
        return False
    return True

# Checks for valid headers from client
# Param lines: Message from client split into array of lines
# Return: True if headers are properly formatted, otherwise False
def checkHeaders(lines):
    if len(lines) > 2:
        headers = lines[1:-1]
        for h in headers:
            split = h.split(": ")
            if len(split) != 2 or " " in split[0]:
                return None
    return True

# Reformats the message from client to relative URL + Host header format
# Param mes: Request message from client
# Param path: Absolute url from client request
# Return: Message to be sent to server
def formatMessage(mes, path):
    url = urlparse(path)
    message = "GET " + url.path + " HTTP/1.0\r\n" + "Host: " + url.hostname + "\r\n" + "Connection: close\r\n"
    lines = mes.splitlines()
    # Append headers
    if len(lines) > 2:
        headers = lines[1:-1]
        for h in headers:
            split = h.split(": ")
            if split[0] != "Connection":
                message += h + "\r\n"
    message = message.encode()
    # Check cache
    if caching:
        if path in cache:
            message += b"If-Modified-Since: " + cache[path][0] + b"\r\n"
    message += b"\r\n"
    return message

# Checks if the message from server is complete
# Param mes: Message from server
# Param bodySize: Length of message body specified by server header
# Return: True if message is complete, otherwise False
def bodyComplete(mes, bodySize):
    if len(mes.split(b"\r\n\r\n")[1]) == bodySize:
        return True
    return False

# Reads and responds to message from client then closes connection
# Param clientSocket: Connection socket with client
def handleConnection(clientSocket):
    message = ""
    while not message.endswith("\r\n\r\n"):
        message += clientSocket.recv(2048).decode()
    
    path = checkMessage(message)

    if path == 200:
        clientSocket.send("HTTP/1.0 200 OK".encode())
    elif path == 400:
        clientSocket.send("HTTP/1.0 400 Bad Request".encode())
    elif path == 403:
        clientSocket.send("HTTP/1.0 403 Forbidden".encode())
    elif path == 501:
        clientSocket.send("HTTP/1.0 501 Not Implemented".encode())
    else:
        # Connect to server
        url = urlparse(path)
        serverPort = url.port if url.port else 80
        serverSocket = socket(AF_INET, SOCK_STREAM)
        serverSocket.connect((url.hostname, serverPort))

        # Reformat message and send to server
        toServer = formatMessage(message, path)
        serverSocket.send(toServer)

        # Receive message from server and send to client
        bodySize = 0
        toClient = b""
        while b"\r\n\r\n" not in toClient:
            toClient += serverSocket.recv(2048)
        
        if b"Content-Length" in toClient:
            for item in toClient.split(b"\r\n"):
                if b"Content-Length" in item:
                    bodySize = int(item.split(b": ")[1])
                    break
            while not bodyComplete(toClient, bodySize):
                toClient += serverSocket.recv(2048)
        
        global caching
        global cache
        
        if caching:
            # Send from cache
            if b"304 Not Modified" in toClient:
                toClient = cache[path][1]
            # Update cache
            elif b"200 OK" in toClient:
                date = b""
                # Get date from server response
                for item in toClient.split(b"\r\n"):
                    if b"Date:" in item:
                        date = (item.split(b": ")[1])
                # Do not cache if no date header in server response
                if date != b"":
                    cache[path] = [date, toClient]

        clientSocket.send(toClient)
        serverSocket.close()

    clientSocket.close()

# Start of program execution
# Parse out the command line server address and port number to listen to
parser = OptionParser()
parser.add_option('-p', type='int', dest='serverPort')
parser.add_option('-a', type='string', dest='serverAddress')
(options, args) = parser.parse_args()

port = options.serverPort
address = options.serverAddress
if address is None:
    address = 'localhost'
if port is None:
    port = 2100

# Set up signal handling (ctrl-c)
signal.signal(signal.SIGINT, ctrl_c_pressed)

# Set up sockets to receive requests
# Setup listening socket
with socket(AF_INET, SOCK_STREAM) as listenSocket:
    listenSocket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    listenSocket.bind(('', port))
    listenSocket.listen()

    while True:
        # Accept and handle connections
        clientSocket, addr = listenSocket.accept()
        start_new_thread(handleConnection, (clientSocket,))