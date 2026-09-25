"""Provider contract consuming a validated story and a reserved local attempt."""

from abc import ABC, abstractmethod

from ..state import PublicationRecord, PublicationRepository
from ..story import Story


class Publisher(ABC):
    """Adapters persist outcomes using the supplied repository.

    The application must call repository.begin_attempt(story, publisher.name)
    before prepare_draft, so duplicate protection precedes remote side effects.
    Uncertain outcomes must block retries until reconciliation. Final publish
    is deliberately a separate operation from draft preparation.
    """

    def __init__(self, repository: PublicationRepository):
        self.repository = repository

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable destination key, e.g. substack."""

    @abstractmethod
    def prepare_draft(self, story: Story, attempt: PublicationRecord) -> PublicationRecord:
        """Create a draft for a reserved attempt and persist its outcome."""

    @abstractmethod
    def publish(self, story: Story, draft: PublicationRecord) -> PublicationRecord:
        """Publish an existing draft only when explicitly requested."""
