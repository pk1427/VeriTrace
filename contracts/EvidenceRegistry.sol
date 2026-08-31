// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title EvidenceRegistry (Phase 3 scaffold — not implemented yet)
 * @notice Stores tamper-evident SHA-256 evidence fingerprints submitted by the
 *         VeriTrace pipeline. Each record binds an enrolled subject, an
 *         evidence hash, and a block timestamp.
 *
 * PHASE 1 does NOT deploy or use this contract. It is a placeholder so the
 * repo layout matches the planned structure.
 */
contract EvidenceRegistry {
    struct Record {
        bytes32 subjectId;
        bytes32 sha256;      // SHA-256 fingerprint of the evidence payload
        uint256 createdAt;
        address submitter;
    }

    mapping(bytes32 => Record) public records;
    mapping(address => bool) public authorizedSubmitters;

    event RecordAdded(bytes32 indexed subjectId, bytes32 indexed sha256, address indexed submitter);

    modifier onlyAuthorized() {
        require(authorizedSubmitters[msg.sender], "not authorized");
        _;
    }

    // Intentionally minimal — full implementation in Phase 3.
    function addRecord(bytes32 subjectId, bytes32 sha256) external onlyAuthorized {
        require(records[sha256].createdAt == 0, "evidence already recorded");
        records[sha256] = Record(subjectId, sha256, block.timestamp, msg.sender);
        emit RecordAdded(subjectId, sha256, msg.sender);
    }
}
