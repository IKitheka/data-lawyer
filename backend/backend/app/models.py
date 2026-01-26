from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, DateTime, Boolean, ForeignKey, UniqueConstraint, func

class Base(DeclarativeBase):
    pass

class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())

    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="account")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)

    # Store ONLY a hash, never the raw key.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    # Plan names: free / pro / business
    plan: Mapped[str] = mapped_column(String(32), default="free", nullable=False)

    # Daily quota per key
    daily_quota: Mapped[int] = mapped_column(Integer, default=20, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Stripe subscription tracking (optional)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_price_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped["Account"] = relationship(back_populates="api_keys")


class UsageDaily(Base):
    __tablename__ = "usage_daily"
    __table_args__ = (
        UniqueConstraint("api_key_id", "day", name="uq_usage_api_key_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id"), nullable=False)

    # ISO date string YYYY-MM-DD (simple + fast)
    day: Mapped[str] = mapped_column(String(10), nullable=False)

    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
