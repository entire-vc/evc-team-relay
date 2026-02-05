<script lang="ts">
  import { Dialog as DialogPrimitive } from 'bits-ui';

  type Props = DialogPrimitive.TriggerProps & {
    class?: string;
  };

  let { children, ...restProps }: Props = $props();
</script>

<DialogPrimitive.Trigger {...restProps}>
  {@render children?.()}
</DialogPrimitive.Trigger>
