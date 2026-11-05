"""Offline Taiwan tests never access real services unless explicitly opted in."""

import socket

import pytest


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    if request.node.get_closest_marker("integration"):
        return

    def refuse(*args, **kwargs):
        raise OSError("Network is disabled in offline tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
