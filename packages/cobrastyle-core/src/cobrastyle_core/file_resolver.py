from typing import NamedTuple, Protocol


class ResolvedFile(NamedTuple):
    content: str
    url: str


class FileResolver(Protocol):
    def resolve(self, filename: str) -> ResolvedFile: ...


class InMemoryResolver(FileResolver):
    def __init__(self, resources: dict[str, str]):
        self.resources = resources

    def resolve(self, filename: str) -> ResolvedFile:
        return ResolvedFile(url=filename, content=self.resources[filename])


# TODO: implement file resolvers for: fastapi, flask, django, etc.
