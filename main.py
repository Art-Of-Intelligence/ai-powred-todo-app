from typing import List, Optional, Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

"""
Simple FastAPI To‑Do API (in‑memory)
- Create a Task with nested Subtasks in a single request
- List tasks
- Add a subtask to an existing task
- Update (e.g., mark done) a subtask

Run:
  pip install fastapi uvicorn
  uvicorn main:app --reload

Test:
  POST /tasks
  GET  /tasks
  POST /tasks/{task_id}/subtasks
  PATCH /tasks/{task_id}/subtasks/{subtask_id}
"""


# -------------------------------
# Pydantic models
# -------------------------------
class SubtaskBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    allocated_time: int = Field(..., ge=0)
    done: bool = False


class SubtaskCreate(SubtaskBase):
    pass


class Subtask(SubtaskBase):
    id: int


class TaskBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None


class TaskCreate(TaskBase):
    subtasks: List[SubtaskCreate] = []


class Task(TaskBase):
    id: int
    done: bool = False  # true if all subtasks are done (auto-updated)
    subtasks: List[Subtask] = []


class SubtaskUpdate(BaseModel):
    title: Optional[str] = None
    done: Optional[bool] = None


# -------------------------------
# In‑memory store (very simple)
# -------------------------------
TASKS: Dict[int, Task] = {}
_task_id_seq = 0
_subtask_id_seq = 0


def _next_task_id() -> int:
    global _task_id_seq
    _task_id_seq += 1
    return _task_id_seq


def _next_subtask_id() -> int:
    global _subtask_id_seq
    _subtask_id_seq += 1
    return _subtask_id_seq


# -------------------------------
# FastAPI app
# -------------------------------
app = FastAPI(title="Simple To‑Do API (Tasks & Subtasks)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"message": "To‑Do API is running. See /docs for Swagger UI."}


# Create a task with nested subtasks
@app.post("/tasks", response_model=Task, status_code=201)
async def create_task(payload: TaskCreate):
    task_id = _next_task_id()
    # Build subtasks with IDs
    subs = [
        Subtask(id=_next_subtask_id(), **st.model_dump()) for st in payload.subtasks
    ]
    # A task is done if it has subtasks and all of them are done
    all_done = len(subs) > 0 and all(s.done for s in subs)

    task = Task(
        id=task_id,
        title=payload.title,
        description=payload.description,
        subtasks=subs,
        done=all_done,
    )
    TASKS[task_id] = task
    return task


# List all tasks
@app.get("/tasks", response_model=List[Task])
async def list_tasks():
    return list(TASKS.values())


# Add a subtask to an existing task
@app.post("/tasks/{task_id}/subtasks", response_model=Task, status_code=201)
async def add_subtask(task_id: int, payload: SubtaskCreate):
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    sub = Subtask(id=_next_subtask_id(), **payload.model_dump())
    task.subtasks.append(sub)
    task.done = len(task.subtasks) > 0 and all(s.done for s in task.subtasks)
    TASKS[task_id] = task
    return task


# Update a subtask (e.g., mark done)
@app.patch("/tasks/{task_id}/subtasks/{subtask_id}", response_model=Task)
async def update_subtask(task_id: int, subtask_id: int, payload: SubtaskUpdate):
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    sub = next((s for s in task.subtasks if s.id == subtask_id), None)
    if not sub:
        raise HTTPException(status_code=404, detail="Subtask not found")

    if payload.title is not None:
        sub.title = payload.title
    if payload.done is not None:
        sub.done = payload.done

    task.done = len(task.subtasks) > 0 and all(s.done for s in task.subtasks)
    TASKS[task_id] = task
    return task
