const hre = require("hardhat");

async function main() {
  const factory = await hre.ethers.getContractFactory("EvidenceRegistry");
  const contract = await factory.deploy();
  await contract.waitForDeployment();
  const addr = contract.target || contract.address;
  console.log("EvidenceRegistry deployed to:", addr);
  if (hre.network.name === "amoy") {
    console.log("Verify: npx hardhat verify --network amoy", addr);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode(1);
});
