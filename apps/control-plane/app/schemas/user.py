from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field


class UserBase(BaseModel):
    email: EmailStr
    is_active: bool = True
    is_admin: bool = False


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_active: bool | None = None
    is_admin: bool | None = None


class UserRead(BaseModel):
    id: uuid.UUID
    email: EmailStr
    is_active: bool
    is_admin: bool

    model_config = {"from_attributes": True}
