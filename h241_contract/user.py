from pydantic import BaseModel
from typing import Optional



class Timeslot(BaseModel):
    start_time: str
    end_time: str
    day_of_week: Optional[str] = None


class WorkExperience(BaseModel):
    company: str
    role: str
    start_date: str
    end_date: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    achievements: list[str] = []


class Education(BaseModel):
    institution: str
    degree: str
    field_of_study: str
    start_date: str
    end_date: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    gpa: Optional[float] = None


class User(BaseModel):
    name: str
    web3_ident: str

class ServiceProvider(User):
    email: str
    phone: Optional[str] = None
    skills: list[str] = []
    education: list[Education] = []
    experience: list[WorkExperience] = []
    availability: list[Timeslot] = []
    summary: Optional[str] = None
    location: Optional[str] = None
    social_links: Optional[dict[str, str]] = None

class ServiceConsumer(User):
    email: str
    phone: Optional[str] = None
    skills: list[str] = []
    education: list[Education] = []
    experience: list[WorkExperience] = []
    availability: list[Timeslot] = []
    summary: Optional[str] = None
    location: Optional[str] = None
    social_links: Optional[dict[str, str]] = None

