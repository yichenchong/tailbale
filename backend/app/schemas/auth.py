from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SetupUserRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=8)


class UserResponse(BaseModel):
    id: str
    username: str
    display_name: str | None
    role: str


class LoginResponse(BaseModel):
    user: UserResponse


class AuthStatusResponse(BaseModel):
    setup_complete: bool
    authenticated: bool
