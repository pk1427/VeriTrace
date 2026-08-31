const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("EvidenceRegistry", function () {
  let registry, owner, alice, bob;
  const SUBJECT = ethers.keccak256(ethers.toUtf8Bytes("prasad"));
  const FINGERPRINT = ethers.keccak256(
    ethers.solidityPacked(["string"], ["veritrace-evidence-payload"])
  );

  beforeEach(async function () {
    [owner, alice, bob] = await ethers.getSigners();
    const factory = await ethers.getContractFactory("EvidenceRegistry", owner);
    registry = await factory.deploy();
    await registry.waitForDeployment();
  });

  it("deploys with the deployer as owner and authorized submitter", async function () {
    expect(await registry.owner()).to.equal(owner.address);
    expect(await registry.authorizedSubmitters(owner.address)).to.equal(true);
    expect(await registry.isRecorded(FINGERPRINT)).to.equal(false);
  });

  it("owner can authorize a submitter and an authorized submitter can anchor", async function () {
    await expect(registry.connect(owner).setAuthorized(alice.address, true))
      .to.emit(registry, "SubmitterAuthorized")
      .withArgs(alice.address, true);

    const tx = await registry.connect(alice).addRecord(SUBJECT, FINGERPRINT);
    await expect(tx)
      .to.emit(registry, "RecordAdded")
      .withArgs(SUBJECT, FINGERPRINT, alice.address);

    expect(await registry.isRecorded(FINGERPRINT)).to.equal(true);

    const rec = await registry.verify(FINGERPRINT);
    expect(rec.subjectId).to.equal(SUBJECT);
    expect(rec.sha256).to.equal(FINGERPRINT);
    expect(rec.submitter).to.equal(alice.address);
    expect(typeof rec.createdAt).to.equal("bigint");
  });

  it("rejects re-anchoring the same fingerprint (idempotent)", async function () {
    await registry.connect(owner).setAuthorized(alice.address, true);
    await registry.connect(alice).addRecord(SUBJECT, FINGERPRINT);
    await expect(registry.connect(alice).addRecord(SUBJECT, FINGERPRINT))
      .to.be.revertedWith("EvidenceRegistry: evidence already recorded");
  });

  it("rejects unauthorized submitters", async function () {
    await expect(registry.connect(bob).addRecord(SUBJECT, FINGERPRINT))
      .to.be.revertedWith("EvidenceRegistry: submitter not authorized");
  });

  it("owner can revoke authorization", async function () {
    await registry.connect(owner).setAuthorized(alice.address, true);
    await expect(registry.connect(owner).setAuthorized(alice.address, false))
      .to.emit(registry, "SubmitterAuthorized")
      .withArgs(alice.address, false);
    await expect(registry.connect(alice).addRecord(SUBJECT, FINGERPRINT))
      .to.be.revertedWith("EvidenceRegistry: submitter not authorized");
  });

  it("verify reverts for an unrecorded fingerprint", async function () {
    await expect(registry.verify(FINGERPRINT))
      .to.be.revertedWith("EvidenceRegistry: evidence not recorded");
  });
});
