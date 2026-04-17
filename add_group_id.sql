-- Migration: Add group_id to assignments table
-- Description: Adds a group_id column to track assignments originating from a group.

-- 1. Add group_id column if it doesn't exist
DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='assignments' AND column_name='group_id') THEN
        ALTER TABLE assignments ADD COLUMN group_id INTEGER;
    END IF;
END $$;

-- 2. Add foreign key constraint if it doesn't exist
DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints 
                   WHERE constraint_name='fk_assignments_group_id') THEN
        ALTER TABLE assignments 
        ADD CONSTRAINT fk_assignments_group_id 
        FOREIGN KEY (group_id) REFERENCES employee_groups(id) 
        ON DELETE SET NULL;
    END IF;
END $$;
