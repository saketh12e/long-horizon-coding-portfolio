//! Public example: `cargo run -p deltalake-core --features datafusion --example row_tracking_probe -- read /path`.
use deltalake_core::DeltaTableBuilder;
use futures::TryStreamExt;
use serde_json::json;
use url::Url;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    let operation = args.get(1).ok_or("expected read or enable")?;
    let path = std::fs::canonicalize(args.get(2).ok_or("expected table path")?)?;
    let mut builder = DeltaTableBuilder::from_url(Url::from_directory_path(path).map_err(|_| "invalid table path")?)?;
    if let Some(version) = args.get(3) {
        builder = builder.with_version(version.parse()?);
    }
    let table = builder.load().await?;
    if operation == "enable" {
        let table = table.enable_row_tracking().await?;
        println!("{}", json!({"version": table.version()}));
        return Ok(());
    }
    if operation != "read" { return Err("unknown operation".into()); }
    let (_, stream) = table.scan_table().with_row_tracking(true).await?;
    let schema: Vec<_> = stream.schema().fields().iter().map(|f| json!({
        "name": f.name(), "type": format!("{:?}", f.data_type()), "nullable": f.is_nullable()
    })).collect();
    let batches: Vec<_> = stream.try_collect().await?;
    let mut writer = arrow_json::ArrayWriter::new(Vec::new());
    writer.write_batches(&batches.iter().collect::<Vec<_>>())?;
    writer.finish()?;
    let rows: serde_json::Value = serde_json::from_slice(&writer.into_inner())?;
    println!("{}", json!({"rows": rows, "schema": schema}));
    Ok(())
}
