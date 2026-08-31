// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title EvidenceRegistry
 * @notice Tamper-evident on-chain anchor for VeriTrace evidence fingerprints.
 * @dev Each immutable record binds an enrolled subjectId to a SHA-256 fingerprint
 *      (bytes32) of an evidence image. One fingerprint => one record (idempotent).
 *      Only owner-authorized submitters may write; anyone can read/verify.
 *
 * Phase 1 does NOT deploy or use this contract — it is scaffolding finalized for
 * Phase 3 (evidence + chain).
 */
contract EvidenceRegistry {
    struct Record {
        bytes32 subjectId;   // deterministic bytes32 mapping of the enrolled subject_id
        bytes32 sha256;      // SHA-256 fingerprint of the evidence payload
        uint256 createdAt;
        address submitter;
    }

    address public owner;
    mapping(address => bool) public authorizedSubmitters;
    mapping(bytes32 => Record) public records;

    event RecordAdded(bytes32 indexed subjectId, bytes32 indexed sha256, address indexed submitter);
    event SubmitterAuthorized(address indexed submitter, bool authorized);

    modifier onlyOwner() {
        require(msg.sender == owner, "EvidenceRegistry: caller is not the owner");
        _;
    }

    modifier onlyAuthorized() {
        require(authorizedSubmitters[msg.sender], "EvidenceRegistry: submitter not authorized");
        _;
    }

    constructor() {
        owner = msg.sender;
        authorizedSubmitters[owner] = true;
    }

    function setAuthorized(address submitter, bool authorized) external onlyOwner {
        authorizedSubmitters[submitter] = authorized;
        emit SubmitterAuthorized(submitter, authorized);
    }

    /**
     * @notice Anchor an evidence fingerprint. Re-anchoring the same sha256 reverts
     *         ("evidence already recorded") — the on-chain record is the single
     *         source of truth for whether a fingerprint was planted.
     */
    function addRecord(bytes32 subjectId, bytes32 fingerprint) external onlyAuthorized {
        require(records[fingerprint].createdAt == 0, "EvidenceRegistry: evidence already recorded");
        records[fingerprint] = Record(subjectId, fingerprint, block.timestamp, msg.sender);
        emit RecordAdded(subjectId, fingerprint, msg.sender);
    }

    /**
     * @notice Verify a fingerprint on-chain (read-only). Reverts if not recorded;
     *         returns the immutable record so an off-chain party can re-verify
     *         independently by recomputing the image's SHA-256 off-chain.
     */
    function verify(bytes32 fingerprint) external view returns (Record memory) {
        require(records[fingerprint].createdAt != 0, "EvidenceRegistry: evidence not recorded");
        return records[fingerprint];
    }

    function isRecorded(bytes32 fingerprint) external view returns (bool) {
        return records[fingerprint].createdAt != 0;
    }

    function getRecord(bytes32 fingerprint) external view returns (Record memory) {
        return records[fingerprint];
    }
}
