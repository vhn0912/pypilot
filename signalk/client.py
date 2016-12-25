#!/usr/bin/env python
#
#   Copyright (C) 2016 Sean D'Epagnier
#
# This Program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either
# version 3 of the License, or (at your option) any later version.  

import json
import socket
import select
import sys
import os
import time

from values import *
import server

try:
    import serial
except:
    pass

class ConnectionLost(Exception):
    pass

READ_ONLY = select.POLLIN | select.POLLHUP | select.POLLERR

class SignalKClient(object):
    def __init__(self, f_on_connected, host=False, port=False, autoreconnect=False, have_watches=False):
        self.autoreconnect = autoreconnect

        orighost = host
        if not host:
            host = 'pypilot'

        config = {}
        configfilename = os.getenv('HOME') + '/.pypilot/signalk.conf'
        try:
            file = open(configfilename)
            config = json.loads(file.readline())

            if 'host' in config and not orighost:
                host = config['host']

        except IOError:
            try:
                config['host'] = host
                file = open(configfilename, 'w')
                file.write(json.dumps(config) + '\n')
            except IOError:
                print 'failed to write config file:', configfilename

        if '/dev' in host: # serial port
            device, baud = host, port
            if not baud or baud == 21311:
                baud = 9600
            connection = serial.Serial(device, baud)
            cmd = 'stty -F ' + device + ' icanon iexten'
            print 'running', cmd
            os.system(cmd)
        else:
            if not port:
                if ':' in host:
                    i = host.index(':')
                    host = host[:i]
                    port = host[i+1:]
                else:
                    port = server.DEFAULT_PORT
            try:
                connection = socket.create_connection((host, port), 1)
            except:
                print 'connect failed to %s:%d' % (host, port)
                raise

            self.host_port = host, port
        self.f_on_connected = f_on_connected
        self.have_watches = have_watches
        self.onconnected(connection)

    def onconnected(self, connection):
        self.socket = server.LineBufferedNonBlockingSocket(connection)
        self.values = []
        self.msg_queue = []
        self.f_on_connected(self)
        self.poller = select.poll()
        if self.socket:
            fd = self.socket.socket.fileno()
        else:
            fd = self.serial.fileno()
        self.poller.register(fd, READ_ONLY)


    def poll(self, timeout = 0):
        t0 = time.time()
        self.socket.flush()
        events = self.poller.poll(1000.0 * timeout)        
        while events != []:
            event = events.pop()
            fd, flag = event
            if flag & (select.POLLHUP | select.POLLERR):
                raise ConnectionLost
            if flag & select.POLLIN:
                if self.socket:
                    if not self.socket.recv():
                        raise ConnectionLost
            if flag & select.POLLOUT:
                if self.socket:
                    self.socket.flush()
                    #if not self.socket.out_buffer:
                    #    self.poller.register(self.socket.socket, READ_ONLY)

    def send(self, request):
        #if not self.socket.out_buffer:
        #    self.poller.register(self.socket.socket, READ_ONLY | select.POLLOUT)
        self.socket.send(json.dumps(request)+'\n')

    def receive_line(self, timeout = 0):
        try:
            t0 = t1 = time.time()
            if self.socket:
                f = self.socket
            else:
                f = self.serial
            while timeout - (t1 - t0) >= 0:
                self.poll(timeout - (t1 - t0) > 0)
                line = f.readline()
                if line:
#                    msg = json.loads(line.rstrip())
                    try:
                        msg = json.loads(line.rstrip())
                    except:
                        # ignore garbage
                        print 'invalid message from server:', line
                        sys.exit(-2)

                    return msg
            
                #dt = time.time() - t1
                # maybe sleep for up to 10 ms
                #if dt < .01:
                #    time.sleep(.01 - dt)
                t1 = time.time()

        except ConnectionLost:
            self.socket.socket.close()
            if not self.autoreconnect:
                raise ConnectionLost
                
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            while True:
                print 'Disconnected.  Reconnecting in 3...'
                time.sleep(3)
                try:
                    connection.connect(self.host_port)
                    print 'Connected.'
                    break
                except:
                    continue

            self.onconnected(connection)

        return False

    def receive(self, timeout = 0):
        ret = {}
        msg = self.receive_single(timeout)
        while msg:
            name, value = msg
            ret[name] = value
            msg = self.receive_single()
        return ret

    def receive_single(self, timeout = 0):
        if len(self.msg_queue) > 0:
            msg = self.msg_queue[0]
            self.msg_queue = self.msg_queue[1:]
            return msg
        
        line = self.receive_line(timeout)
        if line:
            self.msg_queue += self.flatten_line(line)
            return self.receive_single()
        return False

    def flatten_line(self, line, name_prefix=''):
        msgs = []
        for name in line:
            msg = line[name]
            if type(msg) == type({}):
                if 'value' in msg or 'type' in msg:
                    msgs.append((name_prefix + name, msg))
                else:
                    msgs += self.flatten_line(msg, name_prefix + name + '/')
        return msgs

    def list_values(self):
        request = {'method' : 'list'}
        self.send(request)
        return self.receive(10)

    def get(self, name):
        request = {'method' : 'get', 'name' : name}
        self.send(request)

    def set(self, name, value):
        #request = {'method' : 'set', 'name' : name, 'value' : value}
        #self.send(request)

        print 'value', value
        if type(value) == type(""):
            value = '"' + value + '"'
        request = '{"method": "set", "name": "' + name + '", "value": ' + str(value) + '}\n'
        #print 'request', request
        self.socket.send(request)

    def watch(self, name, value=True):
        request = {'method' : 'watch', 'name' : name, 'value' : value}
        self.send(request)

    def print_values(self):
        if len(self.values) == 0:
            self.values = self.list_values()

        if not self.values:
            return

        for name in sorted(self.values):
            self.get(name)
            result = self.receive(2)
            if result:
                print name, '=', result[name]['value']
            else:
                print 'no result', name

def SignalKClientFromArgs(argv, watch, *cargs):
    host = False
    port = False
    watches = argv[1:]
    if len(argv) > 1:
        if ':' in argv[1]:
            i = argv[1].index(':')
            host = argv[1][:i]
            port = int(argv[1][i+1:])
            watches = watches[1:]
        elif not '/' in argv[1]:
            host = argv[1]
            watches = watches[1:]

    def on_con(client):
        for arg in watches:
            if watch:
                client.watch(arg)
            else:
                client.get(arg)
        if len(cargs) == 1:
            cargs[0]()
            
    return SignalKClient(on_con, host, port, autoreconnect=True, have_watches=watches)

# this simple test client for an autopilot server
# connects, enumerates the values, and then requests
# each value, printing them
def main():
    continuous = '-c' in sys.argv
    if continuous:
        sys.argv.remove('-c')

    client = SignalKClientFromArgs(sys.argv, continuous)
    if not client.have_watches:
        client.print_values()
        exit()

    while True:
        result = client.receive(1000)
        if result:
            print json.dumps(result)
            if not continuous:
                return

if __name__ == '__main__':
    main()
