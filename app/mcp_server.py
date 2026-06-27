import datetime
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP
mcp = FastMCP("pet-pal-mcp")

# In-memory storage for pet care state
VET_SLOTS = [
    "2026-06-28 10:00",
    "2026-06-28 14:00",
    "2026-06-29 11:00",
    "2026-06-29 15:00",
]

MEDICATIONS = [
    {"pet": "Buddy", "name": "Heartgard", "dosage": "1 chewable", "schedule": "Monthly"},
    {"pet": "Luna", "name": "Carprofen", "dosage": "25mg", "schedule": "Every 12 hours"},
]

ROUTINES = {
    "puppy": "Morning: 15-minute walk, feed puppy formula. Afternoon: Nap, potty training. Evening: Socialization, feed, sleep.",
    "adult_dog": "Morning: 30-minute run, feed. Afternoon: Rest. Evening: Playtime, feed, 15-minute walk.",
    "senior_dog": "Morning: 15-minute slow walk, feed. Afternoon: Warm rest. Evening: Gentle play, feed.",
}

@mcp.tool()
def get_vet_availability() -> str:
    """Retrieve available dates and times for veterinarian and grooming appointments.
    
    Returns:
        A list of available appointment slots.
    """
    return "Available appointment slots:\n" + "\n".join(f"- {slot}" for slot in VET_SLOTS)

@mcp.tool()
def book_appointment(pet_name: str, appointment_type: str, date_time: str) -> str:
    """Confirm booking of an appointment for a pet.
    
    Args:
        pet_name: The name of the pet.
        appointment_type: Type of appointment ('vet' or 'grooming').
        date_time: The selected date and time from available slots.
    """
    if date_time in VET_SLOTS:
        VET_SLOTS.remove(date_time)
        return f"Successfully booked a {appointment_type} appointment for {pet_name} on {date_time}."
    return f"Failed to book: slot '{date_time}' is not available or already taken."

@mcp.tool()
def get_medication_schedule(pet_name: str) -> str:
    """Get the medication tracking schedule for a specific pet.
    
    Args:
        pet_name: The name of the pet (e.g. 'Buddy', 'Luna').
    """
    pet_meds = [m for m in MEDICATIONS if m["pet"].lower() == pet_name.lower()]
    if not pet_meds:
        return f"No medications tracked for pet '{pet_name}'."
    return f"Medication schedule for {pet_name}:\n" + "\n".join(
        f"- {m['name']}: {m['dosage']} ({m['schedule']})" for m in pet_meds
    )

@mcp.tool()
def save_medication(pet_name: str, medication_name: str, dosage: str, schedule: str) -> str:
    """Log and save a new medication schedule for a pet.
    
    Args:
        pet_name: Name of the pet.
        medication_name: Name of the medication.
        dosage: Dosage amount.
        schedule: How often to administer (e.g. 'Daily', 'Weekly').
    """
    new_med = {"pet": pet_name, "name": medication_name, "dosage": dosage, "schedule": schedule}
    MEDICATIONS.append(new_med)
    return f"Saved new medication for {pet_name}: {medication_name} ({dosage}) - {schedule}."

@mcp.tool()
def get_care_routine(pet_stage: str) -> str:
    """Get recommended diet and exercise care routines based on pet life stage.
    
    Args:
        pet_stage: Pet life stage ('puppy', 'adult_dog', 'senior_dog').
    """
    stage = pet_stage.lower().strip()
    if stage in ROUTINES:
        return f"Routine for {stage}:\n{ROUTINES[stage]}"
    return f"No default routine found for stage '{pet_stage}'. Available stages: puppy, adult_dog, senior_dog."

if __name__ == "__main__":
    # Start FastMCP server via stdio
    mcp.run()
