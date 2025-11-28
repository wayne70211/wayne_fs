# Wayne File System

## Project Overview

**WayneFS** is a user-space file system implemented from scratch in Python and mounted using FUSE (Filesystem in Userspace).

The primary goal of this project is to simulate and understand the low-level internal mechanisms of a file system. Unlike a simple file wrapper, WayneFS manages a raw binary file acting as a physical disk, manually handling block allocation, metadata management, and crash consistency.

It features advanced storage concepts such as **JBD2-style Journaling**, **Write-Back Caching**, and **Indirect Block Addressing**, making it a robust educational tool for understanding Linux storage stacks.

## Architecture

### System Architecture
The following diagram illustrates the high-level data flow, from the user application down to the physical disk simulation.

```mermaid
graph TD
    %% --- Style Definitions ---
    classDef user fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,rx:10,ry:10;
    classDef vfs fill:#fff9c4,stroke:#fbc02d,stroke-width:2px;
    classDef cache fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,stroke-dasharray: 5 5;
    classDef meta fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px;
    classDef jbd fill:#ffebee,stroke:#c62828,stroke-width:2px;
    classDef disk fill:#e0e0e0,stroke:#424242,stroke-width:2px;
    classDef note fill:#ffffff,stroke:#333333,stroke-dasharray: 2 2;

    %% --- 1. User Space ---
    User[User Application <br/> Test Script]:::user
    
    %% --- 2. WayneFS Logic Container ---
    subgraph WayneFS ["WayneFS Process<br/>(User Space)"]
        direction TB
        
        %% Entry Point
        FUSE_Interface["WayneFS Class<br/>(FUSE Operations)"]:::vfs
        
        %% --- Memory Layer ---
        subgraph Memory_Layer ["Caching Layer (RAM)"]
            direction LR
            DC(Dentry Cache):::cache
            PC[("Page Cache<br/>(Write-Back Mechanism)")]:::cache
            Dirty_State["Dirty Pages<br/>(Wait for flush)"]:::note
            PC -.- Dirty_State
        end

        %% --- Metadata Logic Layer ---
        subgraph Metadata_Layer ["Metadata Logic"]
            direction LR
            SB[SuperBlock]:::meta
            IT[Inode Table]:::meta
            BM[Inode/Data Bitmaps]:::meta
            DirOps[Dir En/Decoder]:::meta
        end

        %% --- Consistency Layer ---
        subgraph JBD2_Layer ["JBD2 Journaling Subsystem"]
            direction TB
            TX["Transaction<br/>(Tracks: write_buffer & ordered_blocks)"]:::jbd
            Journal["Journal Manager<br/>(Controls Commit & Recovery)"]:::jbd
        end

        %% --- Driver Layer ---
        DiskDriver["Disk Class<br/>(Raw I/O Wrapper)"]:::disk
    end

    %% --- 3. Physical Storage ---
    subgraph Storage ["Physical Storage"]
        Img[("waynefs.img<br/>(Binary File)")]:::disk
    end

    %% ==========================================
    %% Connections & Logic Flow
    %% ==========================================

    %% 1. User Interaction
    User -->|Syscalls| FUSE_Interface

    %% 2. Cache Interaction (Read/Write Path)
    FUSE_Interface -->|Lookup Path| DC
    FUSE_Interface -- "1. Write Data (Dirty=True)" --> PC
    
    %% 3. Metadata Parsing
    FUSE_Interface -- Read/Parse --> Metadata_Layer
    Metadata_Layer -.-> PC

    %% 4. Transaction Setup
    FUSE_Interface -- "2. Register Data Dependency" --> TX
    FUSE_Interface -- "3. Buffer Metadata Updates" --> TX
    
    %% 5. JBD2 Commit Process (The Critical Path)
    TX -.->|Linked to| Journal
    
    %% Ordered Mode Enforcement (Red Lines)
    Journal == "4. [Ordered Mode] Flush Dependency" ==> PC
    PC == "5. Sync Data Blocks" ==> DiskDriver
    
    %% Journal Logging
    Journal -- "6. Write Descriptor/Metadata/Commit" --> DiskDriver

    %% Driver Output
    DiskDriver <-->|pread / pwrite / fsync| Img

    %% Layout Adjustments
    DC ~~~ PC
```
### Inode Relationship Map
This diagram depicts how Inodes map to physical data blocks, including the logic for direct and indirect pointers.

```mermaid
graph TD
    %% --- Styles ---
    classDef table fill:#e1f5fe,stroke:#01579b,stroke-width:2px;
    classDef inode fill:#fff9c4,stroke:#fbc02d,stroke-width:2px;
    classDef data fill:#e0e0e0,stroke:#616161,stroke-width:2px;
    classDef indirect fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,stroke-dasharray: 5 5;
    classDef invisible fill:none,stroke:none;

    %% =================================================
    %% 1. Upper Layer: Inode Table Region (Horizontal)
    %% =================================================
    subgraph Inode_Table_Region ["Inode Table Region<br/>(Array on Disk)"]
        direction LR
        T1["Inode #1<br/>(Root Dir)"]:::table
        T2["Inode #2<br/>(File A)"]:::table
        T3["Inode #3<br/>(File B)"]:::table
        T4["..."]:::table
        
        %% Force horizontal layout
        T1 ~~~ T2 ~~~ T3 ~~~ T4
    end

    %% =================================================
    %% 2. Lower Container: Inode Detail and Data (Side-by-Side)
    %% =================================================
    subgraph Lower_Section [" "]
        direction LR
        
        %% --- Left: Inode Detail ---
        subgraph Inode_Detail ["Inside Inode #2<br/>(The Metadata)"]
            direction TB
            Meta["Mode: File<br/>Size: 50KB<br/>Nlink: 1"]:::inode
            
            subgraph Pointers ["Pointers (direct[])"]
                P0["direct[0]"]:::inode
                P1["direct[1]"]:::inode
                P_dots["..."]:::inode
                P9["direct[9]"]:::inode
                P10["direct[10]<br/>(Indirect)"]:::inode
            end
        end

        %% --- Right: Data Region ---
        subgraph Data_Region ["Data Block Region<br/>(Physical Blocks)"]
            direction TB
            
            %% Direct Data
            B100["Block 100<br/>(Data: 'Hello')"]:::data
            B101["Block 101<br/>(Data: 'World')"]:::data
            
            %% Indirect Logic
            IndexBlock["Block 500<br/>(Pointer Table)"]:::indirect
            B600["Block 600<br/>(Data: 'Part 2')"]:::data
            B601["Block 601<br/>(Data: 'Part 3')"]:::data
        end
    end

    %% =================================================
    %% Connection Logic
    %% =================================================
    
    %% Table (Top) connects to Detail (Bottom)
    T2 --> Inode_Detail

    %% Detail (Left) connects to Data (Right)
    P0 -->|points to| B100
    P1 -->|points to| B101
    
    %% Indirect Pointers
    P10 -->|points to| IndexBlock
    IndexBlock -.->|ptr 1| B600
    IndexBlock -.->|ptr 2| B601

    %% Hide the border of the lower container for a cleaner look
    style Lower_Section fill:none,stroke:none;
```

## Development Roadmap

The project is divided into four distinct phases, evolving from a simple synchronous file system to a complex, crash-consistent storage engine.

### ✅ Phase 1: Core Architecture & Basic CRUD
*Goal: Establish the fundamental on-disk layout and enable basic file operations.*

1.  **SuperBlock:** Defined disk partition information and filesystem geometry.
2.  **Inode Table:** Implemented the metadata storage structure mapping inodes to Logical Block Addresses (LBA).
3.  **Bitmaps:** Implemented allocation management for Inodes and Data Blocks.
4.  **Directory Operations:** Implemented `getattr`, `readdir`, `mkdir`, and `rmdir`.
5.  **File Operations:** Implemented `create`, `open`, `write`, and `read`.
6.  **Attributes:** Implemented `truncate`, `rename`, and `utimens` (timestamps).
7.  **Permissions & Links:** Implemented hard `link` and `chmod`.

### ✅ Phase 2: Advanced Features & Journaling
*Goal: Enhance system capabilities with caching, large file support, and basic journaling.*

1.  **Ordered Journaling:** Implemented a Write-Ahead Logging (WAL) mechanism to ensure metadata consistency.
2.  **Caching Layer:** Introduced **Page Cache** for file data and **D-entry Cache** for path lookups to improve read performance.
3.  **Indirect Blocks:** Extended file size limits beyond the initial 12 direct pointers:
    - Implemented **Singly Indirect Blocks** (`direct[10]`).
    - Implemented **Doubly Indirect Blocks** (`direct[11]`).
    - Theoretical max file size expanded to approx. 4GB `(10 + 1024 + 1024*1024) * 4KB`.
4.  **Symbolic Links:** Implemented `symlink` and `readlink`.
5.  **Filesystem Statistics:** Implemented `statfs` for disk usage reporting (`df` command).

### ✅ Phase 3: Performance Optimization & JBD2 Integration
*Goal: Bridge the gap between simulation and real-world OS behavior by optimizing I/O patterns and ensuring crash consistency.*

1.  **Write-Back Caching:**
    - Transitioned from a slow **Write-Through** mechanism (immediate disk writes) to a **Write-Back** strategy.
    - Implemented "Dirty Page" tracking, where data persists in RAM until an explicit `fsync` or journal commit occurs.
2.  **JBD2 "Ordered Mode" Logic:**
    - Refined the journaling mechanism to strictly follow Linux JBD2's **Ordered Mode**.
    - **Dependency Tracking:** The system now tracks which data blocks correspond to a transaction.
    - **Ordering Enforcement:** Ensures that *dirty data blocks are flushed to disk* **before** the associated metadata transaction is committed to the journal, preventing stale data on crash recovery.

### Phase 4: Future Work
1.  **Copy-on-Write (CoW):** Design and implement a CoW mechanism to support snapshots and non-destructive writes.

